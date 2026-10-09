#!/usr/bin/env python3
"""Create the penta J boards inside an explicitly authorized Notion page."""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
MARKER = "penta J 초기 세팅 v1"
BOARDS = [
    ("멤버", "members.csv", "이름", {}),
    ("업무", "tasks.csv", "업무명", {
        "마감일": "date", "결과 링크": "url", "상태": "select",
    }),
    ("콘텐츠", "content.csv", "콘텐츠명", {
        "게시 예정일": "date", "원본 링크": "url", "게시 링크": "url",
        "상태": "select", "게시 후 조회수": "number", "저장": "number", "공유": "number",
    }),
]
OPTIONS = {
    "업무": ["대기", "진행", "검토", "완료"],
    "콘텐츠": ["아이디어", "기획", "제작", "검토", "게시", "회고"],
}


def rich(text):
    return [{"type": "text", "text": {"content": text}}]


def paragraph(text):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich(text)}}


def load_board(filename, title):
    with (ROOT / "notion-import" / filename).open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    if not fields or fields[0] != title or not rows:
        raise ValueError(f"Invalid board CSV: {filename}")
    names = [row[title] for row in rows]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise ValueError(f"Missing or duplicate titles: {filename}")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"Inconsistent CSV columns: {filename}")
    return fields, rows


def page_id(value):
    match = re.search(r"([0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})(?=[/?#]|$)", value, re.I)
    if not match:
        raise ValueError("PENTAJ_NOTION_PARENT_PAGE_ID must be a Notion page ID or page URL")
    return match.group(1)


class Notion:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, body=None):
        payload = None if body is None else json.dumps(body).encode()
        for attempt in range(4):
            time.sleep(0.35)
            request = urllib.request.Request(API + path, data=payload, method=method, headers={
                "Authorization": "Bearer " + self.token,
                "Notion-Version": VERSION,
                "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 3:
                    try:
                        delay = min(60, max(1, int(error.headers.get("Retry-After", "2"))))
                    except ValueError:
                        delay = 2
                    time.sleep(delay)
                    continue
                # Never log the token, request headers, or response bodies.
                raise RuntimeError(f"Notion API returned HTTP {error.code}; check access and configuration") from None
            except urllib.error.URLError:
                # Do not blindly retry writes: a timeout can follow a successful creation.
                raise RuntimeError("Notion API connection failed; check network access and rerun to reuse created items") from None
        raise RuntimeError("Notion API rate limit prevented completion")

    def results(self, path, post=False):
        cursor = None
        while True:
            data = {"page_size": 100}
            if cursor:
                data["start_cursor"] = cursor
            if post:
                response = self.request("POST", path, data)
            else:
                query = urllib.parse.urlencode(data)
                response = self.request("GET", path + "?" + query)
            yield from response["results"]
            if not response.get("has_more"):
                break
            cursor = response["next_cursor"]


def existing_child(children, kind, title):
    matches = [block for block in children if block["type"] == kind and block[kind]["title"] == title]
    if len(matches) > 1:
        raise RuntimeError(f"Duplicate existing item: {title}; resolve manually before rerunning")
    return matches[0]["id"] if matches else None


def create_note(api, parent, children, title, texts):
    if existing_child(children, "child_page", title):
        print("Reused page:", title)
        return
    api.request("POST", "/pages", {
        "parent": {"page_id": parent},
        "properties": {"title": {"title": rich(title)}},
        "children": [paragraph(text) for text in texts],
    })
    print("Created page:", title)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate local inputs without credentials or network requests")
    args = parser.parse_args()
    prepared = [(name, title, types, *load_board(filename, title)) for name, filename, title, types in BOARDS]
    if args.dry_run:
        print("Dry run only: no Notion requests or changes")
        for name, _, _, fields, rows in prepared:
            print(f"{name}: {len(fields)} properties, {len(rows)} initial rows")
        print("Additional pages: 주간 회의, 운영 규칙")
        return

    token = os.environ.get("PENTAJ_NOTION_TOKEN")
    parent = os.environ.get("PENTAJ_NOTION_PARENT_PAGE_ID")
    if not token or not parent:
        raise RuntimeError("Set PENTAJ_NOTION_TOKEN securely and PENTAJ_NOTION_PARENT_PAGE_ID in environment settings")
    parent = page_id(parent)
    api = Notion(token)
    # Validate access before making changes.
    api.request("GET", "/users/me")
    info = api.request("GET", "/pages/" + parent)
    if info.get("archived") or info.get("in_trash"):
        raise RuntimeError("Selected parent page is archived")
    children = list(api.results(f"/blocks/{parent}/children"))
    markers = [block for block in children if block["type"] == "paragraph" and
               "".join(t.get("plain_text", t.get("text", {}).get("content", "")) for t in block["paragraph"].get("rich_text", [])) == MARKER]
    if not markers:
        if children:
            raise RuntimeError("Use an empty parent page; existing content will not be modified")
        api.request("PATCH", f"/blocks/{parent}/children", {"children": [
            paragraph(MARKER),
            paragraph("개안 · 찬 · 리트리버 · 당근 · 업사 | 30일 릴스 계정 성장 → 브랜드 UGC 역제안 → MCN"),
            paragraph("이번 주 목표: 공유 권한 확인, 역할·타깃 결정, 첫 릴스 실험 기획. 강점·가능 시간·수치 목표는 미정."),
        ]})

    for name, title, types, fields, rows in prepared:
        children = list(api.results(f"/blocks/{parent}/children"))
        database = existing_child(children, "child_database", name)
        schema = {field: {"title": {}} if field == title else {types.get(field, "rich_text"): {}} for field in fields}
        if "상태" in types:
            schema["상태"] = {"select": {"options": [{"name": option} for option in OPTIONS[name]]}}
        if database:
            actual = api.request("GET", "/databases/" + database)["properties"]
            if any(field not in actual or actual[field]["type"] != next(iter(schema[field])) for field in fields):
                raise RuntimeError(f"Existing {name} schema differs; preserving it without changes")
        else:
            created = api.request("POST", "/databases", {
                "parent": {"type": "page_id", "page_id": parent},
                "is_inline": True, "title": rich(name), "properties": schema,
            })
            database = created["id"]
            print("Created database:", name)
        records = list(api.results(f"/databases/{database}/query", post=True))
        present = {"".join(part.get("plain_text", "") for part in row["properties"][title]["title"]) for row in records}
        for row in rows:
            if row[title] in present:
                continue
            properties = {}
            for field, value in row.items():
                if not value:
                    continue
                kind = "title" if field == title else types.get(field, "rich_text")
                if kind in ("title", "rich_text"):
                    properties[field] = {kind: rich(value)}
                elif kind == "select":
                    properties[field] = {kind: {"name": value}}
                elif kind == "date":
                    properties[field] = {kind: {"start": value}}
                elif kind == "number":
                    properties[field] = {kind: float(value)}
                else:
                    properties[field] = {kind: value}
            api.request("POST", "/pages", {"parent": {"database_id": database}, "properties": properties})
        records = list(api.results(f"/databases/{database}/query", post=True))
        present = {"".join(part.get("plain_text", "") for part in row["properties"][title]["title"]) for row in records}
        if not {row[title] for row in rows}.issubset(present):
            raise RuntimeError(f"Read-back verification failed: {name}")
        print("Verified initial rows:", name, len(rows))

    children = list(api.results(f"/blocks/{parent}/children"))
    create_note(api, parent, children, "주간 회의", [
        "주 1회 30분: 이번 주 결과 → 막힌 일 → 결정과 이유 → 다음 주 담당자·마감일.",
        "성장 기록: 게시 수, 같은 기간의 조회수·저장·공유, 순증 팔로워. 출발 수치와 첫 주 결과로 30일 목표를 정한다.",
    ])
    create_note(api, parent, children, "운영 규칙", [
        "업무별 담당자 1명·검토자·완료 기준·마감일을 정한다. 결과물 링크를 기록한다.",
        "매일 완료한 일·다음 할 일·막힌 일을 기록하는 방식을 제안한다. 사람별 역할과 점검 파트너는 미정이다.",
        "벌금은 아직 도입하지 않았다. 조건·증빙·예외·금액·상한·이의 제기·보관 담당·사용처에 5명이 합의한 뒤 적용한다.",
        "추가 사업 예산 0원. 합숙은 장소·기간·비용을 추후 결정한다.",
    ])
    children = list(api.results(f"/blocks/{parent}/children"))
    for title in ("주간 회의", "운영 규칙"):
        if not existing_child(children, "child_page", title):
            raise RuntimeError("Page read-back verification failed: " + title)
    print("Notion boards and initial rows verified. Member invitations and ChatGPT app authorization remain separate.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
