"""
Longhorn storage monitor for Cluster Guardian.

Queries the Longhorn API for volume health, node disk status,
and backup state. Degrades gracefully when the API is unreachable.
"""

from typing import Optional

import httpx
import structlog

from .config import settings

logger = structlog.get_logger(__name__)

DEFAULT_LONGHORN_URL = "http://longhorn-frontend.longhorn-system.svc.cluster.local:8000"
REQUEST_TIMEOUT = 15.0


class StorageMonitor:
    """Monitor Longhorn volume and node health via the Longhorn REST API."""

    def __init__(self, longhorn_url: str = DEFAULT_LONGHORN_URL):
        self.base_url = longhorn_url.rstrip("/")

    async def _get(self, path: str) -> dict:
        """Issue a GET request and return the JSON body."""
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    async def get_volumes(self) -> list[dict]:
        """List all Longhorn volumes with health status.

        GET /v1/volumes
        """
        try:
            data = await self._get("/v1/volumes")
        except Exception as exc:
            logger.error("Failed to list Longhorn volumes", error=str(exc))
            return []

        results = []
        for vol in data.get("data", []):
            results.append(
                {
                    "name": vol.get("name"),
                    "state": vol.get("state"),
                    "robustness": vol.get("robustness"),
                    "size": vol.get("size"),
                    "actual_size": vol.get("actualSize"),
                    "replicas": len(vol.get("replicas", [])),
                    "number_of_replicas": vol.get("numberOfReplicas"),
                    "conditions": vol.get("conditions", {}),
                    "frontend": vol.get("frontend"),
                }
            )

        return results

    async def get_degraded_volumes(self) -> list[dict]:
        """Get volumes that are degraded, faulted, or under-replicated."""
        volumes = await self.get_volumes()
        unhealthy = []
        for vol in volumes:
            robustness = vol.get("robustness", "unknown")
            replicas = vol.get("replicas", 0)
            desired = vol.get("number_of_replicas")

            is_degraded = robustness in ("degraded", "faulted", "unknown")
            under_replicated = desired is not None and replicas < int(desired)

            if is_degraded or under_replicated:
                unhealthy.append(vol)

        return unhealthy

    async def get_volume_detail(self, name: str) -> dict:
        """Get detailed info for a specific volume including replica status.

        GET /v1/volumes/{name}
        """
        try:
            data = await self._get(f"/v1/volumes/{name}")
        except Exception as exc:
            logger.error("Failed to get volume detail", name=name, error=str(exc))
            return {"error": str(exc)}

        replicas = []
        for r in data.get("replicas", []):
            replicas.append(
                {
                    "name": r.get("name"),
                    "mode": r.get("mode"),
                    "running": r.get("running"),
                    "host_id": r.get("hostId"),
                    "data_path": r.get("dataPath"),
                    "failed_at": r.get("failedAt"),
                }
            )

        return {
            "name": data.get("name"),
            "state": data.get("state"),
            "robustness": data.get("robustness"),
            "size": data.get("size"),
            "actual_size": data.get("actualSize"),
            "frontend": data.get("frontend"),
            "number_of_replicas": data.get("numberOfReplicas"),
            "conditions": data.get("conditions", {}),
            "replicas": replicas,
            "controllers": data.get("controllers", []),
            "last_backup": data.get("lastBackup"),
            "last_backup_at": data.get("lastBackupAt"),
        }

    async def get_nodes(self) -> list[dict]:
        """Get Longhorn node status including disk health.

        GET /v1/nodes
        """
        try:
            data = await self._get("/v1/nodes")
        except Exception as exc:
            logger.error("Failed to list Longhorn nodes", error=str(exc))
            return []

        results = []
        for node in data.get("data", []):
            disks = {}
            for disk_name, disk in node.get("disks", {}).items():
                disks[disk_name] = {
                    "path": disk.get("path"),
                    "storage_available": disk.get("storageAvailable"),
                    "storage_maximum": disk.get("storageMaximum"),
                    "storage_scheduled": disk.get("storageScheduled"),
                    "allow_scheduling": disk.get("allowScheduling"),
                    "conditions": disk.get("conditions", {}),
                }

            conditions = {}
            for cond_name, cond in node.get("conditions", {}).items():
                conditions[cond_name] = {
                    "status": cond.get("status"),
                    "reason": cond.get("reason"),
                    "message": cond.get("message"),
                }

            results.append(
                {
                    "name": node.get("name"),
                    "ready": node.get("conditions", {}).get("Ready", {}).get("status")
                    == "True",
                    "schedulable": node.get("conditions", {})
                    .get("Schedulable", {})
                    .get("status")
                    == "True",
                    "allow_scheduling": node.get("allowScheduling"),
                    "conditions": conditions,
                    "disks": disks,
                }
            )

        return results

    async def get_backups(self, volume_name: str | None = None) -> list[dict]:
        """List recent backups and their status.

        GET /v1/backupvolumes
        """
        try:
            data = await self._get("/v1/backupvolumes")
        except Exception as exc:
            logger.error("Failed to list backup volumes", error=str(exc))
            return []

        results = []
        for bv in data.get("data", []):
            name = bv.get("name")
            if volume_name and name != volume_name:
                continue
            results.append(
                {
                    "name": name,
                    "last_backup_name": bv.get("lastBackupName"),
                    "last_backup_at": bv.get("lastBackupAt"),
                    "data_stored": bv.get("dataStored"),
                    "messages": bv.get("messages", {}),
                }
            )

        return results

    async def health_check(self) -> bool:
        """Check Longhorn API reachability."""
        try:
            await self._get("/v1")
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_storage_monitor: Optional[StorageMonitor] = None


def get_storage_monitor() -> StorageMonitor:
    """Get or create the StorageMonitor singleton."""
    global _storage_monitor
    if _storage_monitor is None:
        _storage_monitor = StorageMonitor(longhorn_url=settings.longhorn_url)
    return _storage_monitor
