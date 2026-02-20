"""
Qdrant-backed vector memory for Cluster Guardian.

Stores issue+resolution pairs as vectors and recalls similar past issues
during future scans, enabling the agent to learn from previous incidents.
"""

import uuid
import logging
from datetime import datetime, timezone

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from .config import settings

logger = logging.getLogger(__name__)

VECTOR_SIZE = 1536


class VectorMemory:
    """Qdrant-backed vector store for issue/resolution pairs."""

    def __init__(
        self,
        qdrant_url: str,
        litellm_url: str,
        litellm_api_key: str,
    ) -> None:
        self.qdrant_url = qdrant_url
        self.litellm_url = litellm_url.rstrip("/")
        self.litellm_api_key = litellm_api_key
        self.collection = settings.qdrant_collection
        self.available = False
        self._client: AsyncQdrantClient | None = None

    async def connect(self) -> None:
        """Connect to Qdrant and ensure the collection exists."""
        try:
            self._client = AsyncQdrantClient(url=self.qdrant_url)
            collections = await self._client.get_collections()
            existing = [c.name for c in collections.collections]

            if self.collection not in existing:
                await self._client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(
                        size=VECTOR_SIZE,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info("Created Qdrant collection '%s'", self.collection)

            self.available = True
            logger.info("Vector memory connected to %s", self.qdrant_url)
        except Exception:
            self.available = False
            logger.warning(
                "Qdrant unavailable at %s -- vector memory disabled",
                self.qdrant_url,
                exc_info=True,
            )

    async def _get_embedding(self, text: str) -> list[float]:
        """Fetch an embedding vector from LiteLLM's OpenAI-compatible endpoint."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.litellm_url}/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {self.litellm_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.embedding_model,
                    "input": text,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def store_issue(
        self,
        issue_summary: str,
        resolution: str,
        metadata: dict | None = None,
    ) -> None:
        """Embed an issue summary and store it with its resolution in Qdrant."""
        if not self.available:
            logger.debug("Vector memory unavailable -- skipping store")
            return

        try:
            vector = await self._get_embedding(issue_summary)
            point_id = str(uuid.uuid4())
            payload = {
                "issue": issue_summary,
                "resolution": resolution,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metadata": metadata or {},
            }

            await self._client.upsert(
                collection_name=self.collection,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload=payload,
                    )
                ],
            )
            logger.info("Stored issue vector %s", point_id)
        except Exception:
            logger.warning("Failed to store issue vector", exc_info=True)

    async def recall_similar_issues(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        """Embed a query and return the most similar past issues from Qdrant."""
        if not self.available:
            logger.debug("Vector memory unavailable -- skipping recall")
            return []

        try:
            vector = await self._get_embedding(query)
            results = await self._client.query_points(
                collection_name=self.collection,
                query=vector,
                limit=top_k,
                with_payload=True,
            )

            return [
                {
                    "issue": point.payload.get("issue", ""),
                    "resolution": point.payload.get("resolution", ""),
                    "score": point.score,
                    "timestamp": point.payload.get("timestamp", ""),
                }
                for point in results.points
            ]
        except Exception:
            logger.warning("Failed to recall similar issues", exc_info=True)
            return []

    async def health_check(self) -> bool:
        """Return True if Qdrant is reachable."""
        if not self._client:
            return False
        try:
            await self._client.get_collections()
            return True
        except Exception:
            return False


_memory: VectorMemory | None = None


def get_memory() -> VectorMemory:
    """Return a module-level singleton VectorMemory instance."""
    global _memory
    if _memory is None:
        _memory = VectorMemory(
            qdrant_url=settings.qdrant_url,
            litellm_url=settings.llm_base_url,
            litellm_api_key=settings.llm_api_key,
        )
    return _memory
