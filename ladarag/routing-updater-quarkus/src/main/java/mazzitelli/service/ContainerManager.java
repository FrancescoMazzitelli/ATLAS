package mazzitelli.service;

import jakarta.enterprise.context.ApplicationScoped;
import org.jboss.logging.Logger;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.*;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Scanner;
import org.eclipse.microprofile.config.inject.ConfigProperty;

@ApplicationScoped
public class ContainerManager {
    private static final Logger log = Logger.getLogger(ContainerManager.class);

    @ConfigProperty(name = "TARGET_CONTAINER_NAME", defaultValue = "valhalla")
    String CONTAINER_NAME;

    @ConfigProperty(name = "TRAFFIC_TAR_PATH", defaultValue = "/custom_files/traffic.tar")
    String TRAFFIC_TAR_PATH;

    private static final String LOCAL_WORK_PATH    = "/tmp/traffic_work.tar";
    private static final String LOCAL_BACKUP_PATH  = "/tmp/traffic_original.tar";

    private static final int LEVEL_BITS   = 3;
    private static final int TILE_ID_BITS = 22;
    private static final int LEVEL_MASK   = (1 << LEVEL_BITS) - 1;
    private static final int TILE_ID_MASK = (1 << TILE_ID_BITS) - 1;

    private static final int TRAFFIC_HEADER_SIZE = 32;
    private static final int TRAFFIC_SPEED_SIZE  = 8;

    // ─────────────────────────────────────────────
    // Public pipeline: patch
    // ─────────────────────────────────────────────

    public void executeFullPipeline(List<Long> edgeIds, int speed) throws Exception {
        ensureOriginalBackup();

        log.info("PATCH 1. Copy original backup to work file...");
        Files.copy(Path.of(LOCAL_BACKUP_PATH), Path.of(LOCAL_WORK_PATH),
                StandardCopyOption.REPLACE_EXISTING);

        log.info("PATCH 2. Apply in-place patch on work file...");
        applyPatch(LOCAL_WORK_PATH, edgeIds, speed);

        log.info("PATCH 3. Remove old traffic.tar from container...");
        runCommand("docker", "exec", CONTAINER_NAME,
                "rm", "-f", TRAFFIC_TAR_PATH);

        log.info("PATCH 4. Copy patched tar into container...");
        runCommand("docker", "cp", LOCAL_WORK_PATH,
                CONTAINER_NAME + ":" + TRAFFIC_TAR_PATH);

        log.info("PATCH 5. Restart container...");
        runCommand("docker", "restart", CONTAINER_NAME);

        log.info("Done. " + edgeIds.size() + " edges patched at " + speed + " kph.");
    }

    // ─────────────────────────────────────────────
    // Public pipeline: reset
    // ─────────────────────────────────────────────

    public void executeReset() throws Exception {
        ensureOriginalBackup();

        log.info("RESET 1. Remove old traffic.tar from container...");
        runCommand("docker", "exec", CONTAINER_NAME,
                "rm", "-f", TRAFFIC_TAR_PATH);

        log.info("RESET 2. Copy original backup into container...");
        runCommand("docker", "cp", LOCAL_BACKUP_PATH,
                CONTAINER_NAME + ":" + TRAFFIC_TAR_PATH);

        log.info("RESET 3. Restart container...");
        runCommand("docker", "restart", CONTAINER_NAME);

        log.info("Done. Traffic tar restored to original.");
    }

    // ─────────────────────────────────────────────
    // Ensure we have a local original backup
    // ─────────────────────────────────────────────

    private void ensureOriginalBackup() throws Exception {
        File backup = new File(LOCAL_BACKUP_PATH);
        if (backup.exists() && backup.length() > 1_000_000) {
            log.info("Original backup already present (" + backup.length() + " bytes), skipping copy.");
            return;
        }

        log.info("Original backup not found. Generating fresh traffic.tar in container...");
        runCommand("docker", "exec", CONTAINER_NAME,
                "valhalla_build_extract", "-c", "/custom_files/valhalla.json",
                "--with-traffic", "--overwrite");

        log.info("Copying original traffic.tar from container to local backup...");
        runCommand("docker", "cp",
                CONTAINER_NAME + ":" + TRAFFIC_TAR_PATH,
                LOCAL_BACKUP_PATH);

        backup = new File(LOCAL_BACKUP_PATH);
        if (!backup.exists() || backup.length() < 1_000_000) {
            throw new RuntimeException("Backup copy failed or file too small: " + backup.length() + " bytes");
        }
        log.info("Backup saved: " + backup.length() + " bytes at " + LOCAL_BACKUP_PATH);
    }

    // ─────────────────────────────────────────────
    // Binary patch
    // ─────────────────────────────────────────────

    private void applyPatch(String tarPath, List<Long> edgeIds, int speed) throws IOException {
        Map<Integer, Long> tileOffsets = peres_readTarIndex(tarPath);

        try (RandomAccessFile raf = new RandomAccessFile(tarPath, "rw")) {
            for (Long gid : edgeIds) {
                int level   = (int) (gid & LEVEL_MASK);
                int tileId  = (int) ((gid >> LEVEL_BITS) & TILE_ID_MASK);
                int edgeIdx = (int) (gid >> (LEVEL_BITS + TILE_ID_BITS));
                int tid32   = (tileId << LEVEL_BITS) | level;

                if (!tileOffsets.containsKey(tid32)) {
                    log.warnf("Tile not found in index: level=%d tileId=%d", level, tileId);
                    continue;
                }

                long tileOffsetInTar = tileOffsets.get(tid32);

                // Read edge_count from TrafficTileHeader (offset +16 inside the header)
                raf.seek(tileOffsetInTar + 16);
                byte[] edgeCountBuf = new byte[4];
                raf.read(edgeCountBuf);
                int edgeCount = ByteBuffer.wrap(edgeCountBuf).order(ByteOrder.LITTLE_ENDIAN).getInt();

                if (edgeIdx >= edgeCount) {
                    log.warnf("edge_idx=%d out of range (edge_count=%d) in tile (%d,%d)",
                            edgeIdx, edgeCount, level, tileId);
                    continue;
                }

                long speedOffset = tileOffsetInTar + TRAFFIC_HEADER_SIZE + ((long) edgeIdx * TRAFFIC_SPEED_SIZE);
                ByteBuffer buf = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN);
                buf.putLong(encodeSpeed(speed));

                raf.seek(speedOffset);
                raf.write(buf.array());
            }
        }
    }

    private long encodeSpeed(int speedKph) {
        long overall, s1, s2, s3, bp1, cong1;

        if (speedKph <= 0) {
            overall = 0L;
            s1      = 0L;
            s2      = 127L;
            s3      = 127L;
            bp1     = 0L;
            cong1   = 63L;
        } else {
            long raw = Math.min(Math.max(speedKph / 2, 1), 126);
            overall = raw;
            s1      = raw;
            s2      = 127L;
            s3      = 127L;
            bp1     = 255L;
            cong1   = 0L;
        }

        return (overall        & 0x7FL)        |
               ((s1            & 0x7FL) <<  7) |
               ((s2            & 0x7FL) << 14) |
               ((s3            & 0x7FL) << 21) |
               ((bp1           & 0xFFL) << 28) |
               ((cong1         & 0x3FL) << 44);
    }

    private Map<Integer, Long> peres_readTarIndex(String tarPath) throws IOException {
        Map<Integer, Long> index = new HashMap<>();
        try (RandomAccessFile raf = new RandomAccessFile(tarPath, "r")) {
            byte[] header = new byte[512];
            while (raf.read(header) == 512) {
                String fileName = new String(header, 0, 100).trim();
                if (fileName.isEmpty()) break;

                String sizeStr = new String(header, 124, 12).trim();
                if (sizeStr.isEmpty()) break;
                long size = Long.parseLong(sizeStr, 8);

                if (fileName.equals("index.bin")) {
                    for (int i = 0; i < size; i += 16) {
                        byte[] entry = new byte[16];
                        if (raf.read(entry) < 16) break;
                        ByteBuffer bb = ByteBuffer.wrap(entry).order(ByteOrder.LITTLE_ENDIAN);
                        long offsetInTar = bb.getLong(); // 8 byte: tile offset
                        int  tid32       = bb.getInt();  // 4 byte: tile id
                        bb.getInt();                     // 4 byte: size (skip)
                        index.put(tid32, offsetInTar);
                    }
                    break;
                }

                long skip = ((size + 511) / 512) * 512;
                raf.seek(raf.getFilePointer() + skip);
            }
        }
        return index;
    }

    // ─────────────────────────────────────────────
    // Process runner
    // ─────────────────────────────────────────────

    private void runCommand(String... args) throws IOException, InterruptedException {
        ProcessBuilder pb = new ProcessBuilder(args);
        Process p = pb.start();

        new Thread(() -> {
            try (Scanner s = new Scanner(p.getInputStream())) {
                while (s.hasNextLine()) log.info(s.nextLine());
            }
        }).start();

        new Thread(() -> {
            try (Scanner s = new Scanner(p.getErrorStream())) {
                while (s.hasNextLine()) log.error(s.nextLine());
            }
        }).start();

        int exitCode = p.waitFor();
        if (exitCode != 0) {
            throw new RuntimeException("Command failed: " + String.join(" ", args) + " | exit: " + exitCode);
        }
    }
}