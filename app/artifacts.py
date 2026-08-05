"""テスト検証用のアップロード・解析成果物を一時保存します。"""

import json
import os
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any


class TempArtifactStore:
    """PDF ごとに独立した一時フォルダへ検証用データを保存します。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._operations: dict[str, Path] = {}

    def create_upload(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str,
        analyze_options: dict[str, str] | None = None,
    ) -> Path | None:
        """PDF の場合だけアップロード単位の保存先を作成します。"""
        if content_type != "application/pdf":
            return None
        directory = Path(tempfile.mkdtemp(prefix="upload-", dir=self.root))
        self._write_bytes(directory / "input.pdf", content)
        self.write_json(
            directory,
            "metadata.json",
            {
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(content),
                "sha256": sha256(content).hexdigest(),
                "created_at": datetime.now(UTC).isoformat(),
                "operation_id": None,
                "analyze_options": analyze_options or {},
            },
        )
        return directory

    def associate_operation(self, directory: Path | None, operation_id: str) -> None:
        if directory is None:
            return
        metadata = self.read_json(directory, "metadata.json")
        metadata["operation_id"] = operation_id
        self.write_json(directory, "metadata.json", metadata)
        self._operations[operation_id] = directory

    def find_operation(self, operation_id: str) -> Path | None:
        directory = self._operations.get(operation_id)
        if directory is not None:
            return directory
        for candidate in self.root.glob("upload-*"):
            if not candidate.is_dir():
                continue
            try:
                metadata = self.read_json(candidate, "metadata.json")
            except (OSError, json.JSONDecodeError):
                continue
            if metadata.get("operation_id") == operation_id:
                self._operations[operation_id] = candidate
                return candidate
        return None

    def write_json(
        self, directory: Path | None, filename: str, payload: Any
    ) -> None:
        if directory is None:
            return
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode("utf-8")
        self._write_bytes(directory / filename, encoded)

    def read_json(self, directory: Path, filename: str) -> dict[str, Any]:
        with (directory / filename).open(encoding="utf-8") as source:
            payload = json.load(source)
        return payload if isinstance(payload, dict) else {}

    def write_error(
        self,
        directory: Path | None,
        *,
        stage: str,
        code: str,
        message: str,
    ) -> None:
        self.write_json(
            directory,
            "error.json",
            {
                "stage": stage,
                "code": code,
                "message": message,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    @staticmethod
    def _write_bytes(path: Path, content: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as destination:
                destination.write(content)
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
