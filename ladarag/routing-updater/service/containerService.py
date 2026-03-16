import subprocess
from utils import containerUtils as cu


class ContainerService:

    @staticmethod
    def run_build_extract():
        cmd = [
            "docker",
            "exec",
            cu.CONTAINER_NAME,
            "valhalla_build_extract",
            "-c",
            cu.VALHALLA_CONFIG,
            "--with-traffic",
            "--overwrite"
        ]

        subprocess.run(cmd, check=True)

    @staticmethod
    def copy_tar_from_container():
        cmd = [
            "docker",
            "cp",
            f"{cu.CONTAINER_NAME}:{cu.CONTAINER_TRAFFIC_PATH}",
            cu.LOCAL_TMP_TRAFFIC
        ]
        subprocess.run(cmd, check=True)

        return cu.LOCAL_TMP_TRAFFIC

    @staticmethod
    def copy_tar_to_container():

        cmd = [
            "docker",
            "cp",
            cu.LOCAL_TMP_TRAFFIC,
            f"{cu.CONTAINER_NAME}:{cu.CONTAINER_TRAFFIC_PATH}"
        ]

        subprocess.run(cmd, check=True)

    @staticmethod
    def restart_container():

        cmd = ["docker", "restart", cu.CONTAINER_NAME]

        subprocess.run(cmd, check=True)