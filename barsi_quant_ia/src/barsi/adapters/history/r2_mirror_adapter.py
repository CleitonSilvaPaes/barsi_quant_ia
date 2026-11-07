"""Adapter Decorator para Espelhamento Offsite no Cloudflare R2 (S3-compatible)."""
import json
import boto3
import asyncio
from datetime import datetime
from barsi.ports.history import HistoryPort
from barsi.domain.models import Position, InsightResult, RunSummary
from barsi.infra.logging import logger

class R2MirrorHistoryAdapter(HistoryPort):
    def __init__(
        self,
        inner: HistoryPort,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str = "barsi-history"
    ):
        self.inner = inner
        self.account_id = account_id
        self.bucket_name = bucket_name
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto"
        )

    async def get_last_analysis(self, ticker: str):
        return await self.inner.get_last_analysis(ticker)

    async def save_analysis(self, run_id: str, posicao: Position, insight: InsightResult, noticias: str) -> None:
        await self.inner.save_analysis(run_id, posicao, insight, noticias)

    async def record_run(self, summary: RunSummary) -> None:
        await self.inner.record_run(summary)
        
        # Espelha o resumo da execução no R2 em formato JSONL append-only
        def _upload_r2():
            try:
                now = datetime.utcnow()
                key = f"history/{now.strftime('%Y/%m/%d')}/{summary.run_id}.jsonl"
                payload = json.dumps(summary.model_dump(), default=str)
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=key,
                    Body=payload.encode("utf-8"),
                    ContentType="application/jsonl"
                )
                logger.info("r2_mirror_uploaded", key=key)
            except Exception as exc:
                logger.error("r2_mirror_upload_failed", error=str(exc))

        await asyncio.to_thread(_upload_r2)

