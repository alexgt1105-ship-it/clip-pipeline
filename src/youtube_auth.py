from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import YOUTUBE_CLIENT_SECRET_PATH, YOUTUBE_TOKEN_PATH

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def get_credentials() -> Credentials:
    creds = None
    if YOUTUBE_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(YOUTUBE_TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not YOUTUBE_CLIENT_SECRET_PATH.exists():
                raise FileNotFoundError(
                    f"Missing {YOUTUBE_CLIENT_SECRET_PATH.name}. Create an OAuth desktop "
                    "client in Google Cloud Console for the NEW YouTube account and save "
                    "the JSON there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(YOUTUBE_CLIENT_SECRET_PATH), SCOPES
            )
            # Opens a browser. Sign in with the NEW clipping account here, not the
            # motivation-shorts one.
            creds = flow.run_local_server(port=0)
        YOUTUBE_TOKEN_PATH.write_text(creds.to_json())

    return creds


if __name__ == "__main__":
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", credentials=get_credentials())
    me = youtube.channels().list(part="snippet", mine=True).execute()
    items = me.get("items") or []
    if not items:
        print("Authorised, but this Google account has no YouTube channel yet.")
    else:
        snippet = items[0]["snippet"]
        print(f"Authorised as: {snippet['title']}  (channel {items[0]['id']})")
        print("If that is not the new clipping channel, delete youtube_token.json and rerun.")
