from __future__ import annotations

from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from config import YOUTUBE_PRIVACY_STATUS
from youtube_auth import get_credentials


def upload_short(video_path: Path, title: str, description: str, tags: list[str]) -> str:
    creds = get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:4900],
            "tags": tags[:15],
            "categoryId": "24",  # Entertainment
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()
    return response["id"]
