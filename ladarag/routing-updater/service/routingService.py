from utils.trafficUtils import (
    patch_tar,
    inspect_edges
)
from containerService import ContainerService

class TrafficService:

    @staticmethod
    def build_patch_restart(edge_ids, speed):

        ContainerService.run_build_extract()

        tar_path = ContainerService.copy_tar_from_container()

        patch_tar(tar_path, edge_ids, speed, False)

        ContainerService.copy_tar_to_container()

        ContainerService.restart_container()

        return {
            "status": "completed",
            "patched_edges": len(edge_ids)
        }
    
    @staticmethod
    def patch(tar_path, edge_ids, speed):
        patch_tar(tar_path, edge_ids, speed, False)
        return f"{len(edge_ids)} edges patched"

    @staticmethod
    def reset(tar_path, edge_ids):
        patch_tar(tar_path, edge_ids, None, False)
        return f"{len(edge_ids)} edges reset"

    @staticmethod
    def inspect(tar_path, edge_ids):
        return inspect_edges(tar_path, edge_ids)