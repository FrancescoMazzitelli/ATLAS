package mazzitelli.service;

import jakarta.enterprise.context.ApplicationScoped;
import org.jboss.logging.Logger;
import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
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

    private final String LOCAL_TAR_PATH = "/tmp/traffic.tar";

    private static final int LEVEL_BITS = 3;
    private static final int TILE_ID_BITS = 22;
    private static final int LEVEL_MASK = (1 << LEVEL_BITS) - 1;
    private static final int TILE_ID_MASK = (1 << TILE_ID_BITS) - 1;
    
    private static final int TRAFFIC_HEADER_SIZE = 32;
    private static final int TRAFFIC_SPEED_SIZE = 8;

    public void executeFullPipeline(List<Long> edgeIds, int speed) throws Exception {
        log.info("1. Generate base traffic.tar in the routing engine container...");
        runCommand("docker exec " + CONTAINER_NAME + " valhalla_build_extract -c /custom_files/valhalla.json --with-traffic --overwrite");

        log.info("2. Copy tar from routing engine to here...");
        runCommand("docker cp " + CONTAINER_NAME + ":" + TRAFFIC_TAR_PATH + " " + LOCAL_TAR_PATH);

        log.info("3. Apply in-place patch...");
        applyPatch(LOCAL_TAR_PATH, edgeIds, speed);

        log.info("4. Copy patched tar back to the routing engine container...");
        runCommand("docker cp " + LOCAL_TAR_PATH + " " + CONTAINER_NAME + ":" + TRAFFIC_TAR_PATH);

        log.info("5. Restart the routing engine container...");
        runCommand("docker restart " + CONTAINER_NAME);
    }

    private void applyPatch(String tarPath, List<Long> edgeIds, int speed) throws IOException {
        Map<Integer, Long> tileOffsets = peres_readTarIndex(tarPath);

        try (RandomAccessFile raf = new RandomAccessFile(tarPath, "rw")) {
            for (Long gid : edgeIds) {
                int level = (int) (gid & LEVEL_MASK);
                int tileId = (int) ((gid >> LEVEL_BITS) & TILE_ID_MASK);
                int edgeIdx = (int) (gid >> (LEVEL_BITS + TILE_ID_BITS));
                
                int tid32 = (tileId << LEVEL_BITS) | level;

                if (tileOffsets.containsKey(tid32)) {
                    long tileOffsetInTar = tileOffsets.get(tid32);
                    long speedOffset = tileOffsetInTar + TRAFFIC_HEADER_SIZE + ((long) edgeIdx * TRAFFIC_SPEED_SIZE);
                    
                    long encodedSpeed = encodeSpeed(speed);
                    ByteBuffer buf = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN);
                    buf.putLong(encodedSpeed);
                    
                    raf.seek(speedOffset);
                    raf.write(buf.array());
                }
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

        return (overall & 0x7FL)          |
            ((s1   & 0x7FL) << 7)         |
            ((s2   & 0x7FL) << 14)        |
            ((s3   & 0x7FL) << 21)        |
            ((bp1  & 0xFFL) << 28)        |
            ((cong1 & 0x3FL) << 44);
    }

    private Map<Integer, Long> peres_readTarIndex(String tarPath) throws IOException {
        Map<Integer, Long> index = new HashMap<>();
        try (RandomAccessFile raf = new RandomAccessFile(tarPath, "r")) {
            byte[] header = new byte[512];
            while (raf.read(header) != -1) {
                String fileName = new String(header, 0, 100).trim();
                long size = Long.parseLong(new String(header, 124, 12).trim(), 8);
                
                if (fileName.equals("index.bin")) {
                    long currentPos = raf.getFilePointer();
                    for (int i = 0; i < size; i += 16) {
                        byte[] entry = new byte[16];
                        raf.read(entry);
                        ByteBuffer bb = ByteBuffer.wrap(entry).order(ByteOrder.LITTLE_ENDIAN);
                        long offsetInTar = bb.getLong();
                        int tid32 = bb.getInt();
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

    private void runCommand(String cmd) throws IOException, InterruptedException {
        Process p = Runtime.getRuntime().exec(cmd);

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
            throw new RuntimeException("Comando fallito: " + cmd + " codice: " + exitCode);
        }
    }
}