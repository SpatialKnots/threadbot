from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx


LOGGER = logging.getLogger(__name__)
TWOCH_BASE_URL = "https://2ch.hk"
NUMBER_SIGN = chr(0x2116)
POST_NUMBER_RE = re.compile(f"(?:{NUMBER_SIGN}|#)\\s*(\\d{{5,12}})")


@dataclass(frozen=True)
class TwochOriginal:
    board: str
    thread: int
    post_num: int
    url: str


def extract_2ch_post_numbers(text: str, limit: int = 5) -> list[int]:
    numbers: list[int] = []
    seen: set[int] = set()
    for match in POST_NUMBER_RE.finditer(text or ""):
        number = int(match.group(1))
        if number in seen:
            continue
        seen.add(number)
        numbers.append(number)
        if len(numbers) >= limit:
            break
    return numbers


def build_2ch_post_url(board: str, thread: int, post_num: int, base_url: str = TWOCH_BASE_URL) -> str:
    return f"{base_url.rstrip('/')}/{board}/res/{thread}.html#{post_num}"


def _exception_label(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc!r}"


class TwochClient:
    def __init__(self, base_url: str = TWOCH_BASE_URL, timeout: float = 12.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def fetch_board_ids(self) -> list[str]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.get("/api/mobile/v2/boards")
            response.raise_for_status()
            data = response.json()
        boards = data.get("boards") if isinstance(data, dict) else data
        ids: list[str] = []
        for board in boards or []:
            board_id = board.get("id") if isinstance(board, dict) else None
            if isinstance(board_id, str) and board_id:
                ids.append(board_id)
        return ids

    async def fetch_post(self, board: str, post_num: int) -> dict[str, Any] | None:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            response = await client.get(f"/api/mobile/v2/post/{board}/{post_num}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
        if isinstance(data, dict) and data.get("result") == 1 and isinstance(data.get("post"), dict):
            return data["post"]
        return None

    async def find_original_by_post_number(
        self,
        post_num: int,
        board_ids: list[str] | None = None,
    ) -> TwochOriginal | None:
        boards = board_ids
        if boards is None:
            try:
                boards = await self.fetch_board_ids()
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                LOGGER.warning("Could not fetch 2ch board list, falling back to /b/: %s", _exception_label(exc))
                boards = ["b"]

        for board in boards:
            try:
                post = await self.fetch_post(board, post_num)
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                LOGGER.info(
                    "2ch post lookup failed for board=%s post=%s: %s",
                    board,
                    post_num,
                    _exception_label(exc),
                )
                continue
            if post is None:
                continue
            parent = int(post.get("parent") or 0)
            found_num = int(post.get("num") or post_num)
            thread = parent if parent > 0 else found_num
            post_board = str(post.get("board") or board)
            return TwochOriginal(
                board=post_board,
                thread=thread,
                post_num=found_num,
                url=build_2ch_post_url(post_board, thread, found_num, base_url=self.base_url),
            )
        return None


async def find_original_from_text(
    text: str,
    client: TwochClient | None = None,
    board_ids: list[str] | None = None,
    number_limit: int = 5,
) -> TwochOriginal | None:
    numbers = extract_2ch_post_numbers(text, limit=number_limit)
    if not numbers:
        return None
    active_client = client or TwochClient()
    boards = board_ids
    if boards is None:
        try:
            boards = await active_client.fetch_board_ids()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            LOGGER.warning("Could not fetch 2ch board list, falling back to /b/: %s", _exception_label(exc))
            boards = ["b"]
    for number in numbers:
        original = await active_client.find_original_by_post_number(number, board_ids=boards)
        if original is not None:
            return original
    return None
