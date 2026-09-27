"""
YouTube Shorts Uploader — direct upload via Google API + publishAt scheduling.

The video goes straight to YouTube's servers. No URL needed. Scheduling is native
via the publishAt parameter — set the video to 'private' with a future publish time
and YouTube handles the rest.

Usage (standalone):
    python youtube.py --file clip.mp4 --title "My Short" --description "Check this out"
    python youtube.py --file clip.mp4 --title "My Short" --schedule "2026-04-15 09:00"

Requires:
    pip install google-auth google-auth-oauthlib google-api-python-client
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone

# Google API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Scopes needed for uploading
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube"]

# Maximum retries for resumable upload
MAX_RETRIES = 3


def get_authenticated_service(client_id, client_secret, token_file):
    """
    Authenticate with YouTube API using OAuth2.
    First run opens a browser for consent. Token is cached for future runs.
    """
    creds = None
    token_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), token_file)

    # Load existing token
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Refresh or get new token
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refreshing YouTube token...")
            creds.refresh(Request())
        else:
            print("YouTube authorization required — opening browser...")
            # Build client config from credentials
            client_config = {
                "installed": {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"]
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            creds = flow.run_local_server(port=8080)

        # Save token for next time
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
        print(f"Token saved to {token_path}")

    return build("youtube", "v3", credentials=creds)


def upload_video(youtube, file_path, title, description="", tags=None,
                 category_id="22", privacy="public", schedule_time=None):
    """
    Upload a video to YouTube.

    Args:
        youtube: Authenticated YouTube API service
        file_path: Path to the video file
        title: Video title (max 100 chars)
        description: Video description (max 5000 chars)
        tags: List of tags
        category_id: YouTube category (22 = People & Blogs)
        privacy: 'public', 'private', or 'unlisted'
        schedule_time: ISO 8601 datetime string for scheduled publishing.
                       If set, video is uploaded as 'private' and auto-publishes at this time.

    Returns:
        dict with video id and URL, or None on failure
    """
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        return None

    # If scheduling, force privacy to private
    if schedule_time:
        privacy = "private"
        print(f"Scheduling for: {schedule_time}")

    body = {
        "snippet": {
            "title": title[:100],  # YouTube title limit
            "description": description[:5000],  # YouTube description limit
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        }
    }

    # Add publishAt for scheduling
    if schedule_time:
        body["status"]["publishAt"] = schedule_time

    # Add #Shorts to description if not already there
    if "#shorts" not in description.lower() and "#short" not in description.lower():
        body["snippet"]["description"] += "\n\n#Shorts"

    # Resumable upload
    media = MediaFileUpload(
        file_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024  # 10MB chunks
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    print(f"Uploading to YouTube: {title}")
    print(f"  File: {file_path} ({os.path.getsize(file_path) / 1024 / 1024:.1f} MB)")

    response = None
    retry_count = 0

    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"  Upload progress: {pct}%")
        except Exception as e:
            retry_count += 1
            if retry_count > MAX_RETRIES:
                print(f"Upload failed after {MAX_RETRIES} retries: {e}")
                return None
            print(f"  Retry {retry_count}/{MAX_RETRIES}: {e}")

    video_id = response["id"]
    video_url = f"https://youtube.com/shorts/{video_id}"

    print(f"Upload complete!")
    print(f"  Video ID: {video_id}")
    print(f"  URL: {video_url}")
    if schedule_time:
        print(f"  Scheduled to publish: {schedule_time}")

    return {
        "id": video_id,
        "url": video_url,
        "title": title,
        "scheduled": schedule_time
    }


def upload(file_path, title, description="", tags=None, schedule_time=None,
           config=None):
    """
    High-level upload function called by poster.py.

    Args:
        file_path: Path to video
        title: Video title
        description: Video description
        tags: List of tags
        schedule_time: ISO 8601 datetime for scheduling, or None for immediate
        config: YouTube config dict with client_id, client_secret, token_file

    Returns:
        Result dict or None
    """
    if config is None:
        from config import load_config, get_youtube_config
        full_config = load_config()
        config = get_youtube_config(full_config)

    if not config:
        print("YouTube not configured. Run setup first.")
        return None

    youtube = get_authenticated_service(
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        token_file=config.get("token_file", "tokens/youtube_token.json")
    )

    return upload_video(
        youtube=youtube,
        file_path=file_path,
        title=title,
        description=description,
        tags=tags,
        schedule_time=schedule_time
    )


def main():
    parser = argparse.ArgumentParser(description="Upload video to YouTube Shorts")
    parser.add_argument("--file", "-f", required=True, help="Video file to upload")
    parser.add_argument("--title", "-t", required=True, help="Video title")
    parser.add_argument("--description", "-d", default="", help="Video description")
    parser.add_argument("--tags", nargs="*", default=[], help="Tags/hashtags")
    parser.add_argument("--schedule", help="Schedule time: 'YYYY-MM-DD HH:MM' (your timezone)")
    parser.add_argument("--privacy", default="public", choices=["public", "private", "unlisted"])

    args = parser.parse_args()

    # Convert schedule time to ISO 8601
    schedule_iso = None
    if args.schedule:
        try:
            from zoneinfo import ZoneInfo
            from config import load_config, get_defaults
            cfg = load_config()
            tz_name = os.environ.get("POSTING_TOOL_TIMEZONE") or get_defaults(cfg).get("timezone", "UTC")
            local_tz = ZoneInfo(tz_name)
            dt = datetime.strptime(args.schedule, "%Y-%m-%d %H:%M")
            dt = dt.replace(tzinfo=local_tz)
            schedule_iso = dt.isoformat()
            print(f"Schedule: {args.schedule} ({tz_name}) → {schedule_iso}")
        except Exception as e:
            print(f"Error parsing schedule time: {e}")
            sys.exit(1)

    result = upload(
        file_path=args.file,
        title=args.title,
        description=args.description,
        tags=args.tags,
        schedule_time=schedule_iso
    )

    if result:
        print(f"\nSuccess! {result['url']}")
    else:
        print("\nUpload failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
