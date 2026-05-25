import io
import json
import os
import random
import shutil
import time
from datetime import datetime, timedelta

import docx
import pytz
import tweepy
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Load environment variables
load_dotenv()
CONSUMER_KEY = os.getenv("CONSUMER_KEY")
CONSUMER_SECRET = os.getenv("CONSUMER_SECRET")
ACCESS_KEY = os.getenv("ACCESS_KEY")
ACCESS_SECRET = os.getenv("ACCESS_SECRET")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE")

# Configuration
local_base_folder = "./GLRECS_temp"
image_extensions = (".jpg", ".jpeg", ".png", ".webp", ".gif")
description_extensions = (".txt", ".docx")
MIN_IMAGES_PER_TWEET = 1
MAX_IMAGES_PER_TWEET = 2
RECOMMENDATION_COOLDOWN_DAYS = 7
STATE_DIR = ".bot_state"
RECOMMENDATION_HISTORY_FILE = os.path.join(STATE_DIR, "recommendation_history.json")
SCOPES = ["https://www.googleapis.com/auth/drive"]
miami_tz = pytz.timezone("America/New_York")


def validate_environment():
    required = {
        "CONSUMER_KEY": CONSUMER_KEY,
        "CONSUMER_SECRET": CONSUMER_SECRET,
        "ACCESS_KEY": ACCESS_KEY,
        "ACCESS_SECRET": ACCESS_SECRET,
        "DRIVE_FOLDER_ID": DRIVE_FOLDER_ID,
        "SERVICE_ACCOUNT_FILE": SERVICE_ACCOUNT_FILE,
    }

    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        raise SystemExit(1)

    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"Service account file not found: {SERVICE_ACCOUNT_FILE}")
        raise SystemExit(1)


def ensure_state_dir():
    os.makedirs(STATE_DIR, exist_ok=True)


def load_recommendation_history():
    ensure_state_dir()
    if not os.path.exists(RECOMMENDATION_HISTORY_FILE):
        return {}

    try:
        with open(RECOMMENDATION_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"Could not read history file; starting fresh. {type(e).__name__}: {e}")

    return {}


def save_recommendation_history(history):
    ensure_state_dir()
    with open(RECOMMENDATION_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, sort_keys=True)


def was_recently_recommended(history, folder_id, now):
    entry = history.get(folder_id)
    if not entry:
        return False

    ts_raw = entry.get("last_recommended_at")
    if not ts_raw:
        return False

    try:
        ts = datetime.fromisoformat(ts_raw)
    except ValueError:
        return False

    if ts.tzinfo is None:
        ts = miami_tz.localize(ts)

    return now - ts < timedelta(days=RECOMMENDATION_COOLDOWN_DAYS)


def mark_recommended(history, folder):
    now = datetime.now(miami_tz).isoformat()
    history[folder["id"]] = {
        "folder_name": folder["name"],
        "last_recommended_at": now,
    }
    save_recommendation_history(history)


def is_transient_error(e):
    """Returns True for errors worth retrying."""
    msg = str(e).lower()
    transient_markers = [
        "429",
        "500",
        "502",
        "503",
        "504",
        "service unavailable",
        "too many requests",
        "gateway timeout",
        "connection reset",
        "timed out",
        "temporarily unavailable",
        "over capacity",
    ]
    return any(marker in msg for marker in transient_markers)


def retry_call(
    fn,
    *args,
    max_retries=5,
    initial_delay=5,
    max_delay=90,
    step_name="API call",
    **kwargs,
):
    """Retries transient API failures with exponential backoff + jitter."""
    delay = initial_delay
    last_exception = None

    for attempt in range(1, max_retries + 1):
        try:
            print(f"{step_name}: attempt {attempt}/{max_retries}")
            result = fn(*args, **kwargs)
            print(f"{step_name}: success")
            return result
        except Exception as e:
            last_exception = e
            print(f"{step_name}: failed on attempt {attempt}/{max_retries}")
            print(f"{step_name}: {type(e).__name__}: {e}")

            if attempt == max_retries or not is_transient_error(e):
                raise

            jitter = random.uniform(0.5, 2.0)
            sleep_for = min(delay + jitter, max_delay)
            print(f"{step_name}: retrying in {sleep_for:.1f} seconds...")
            time.sleep(sleep_for)
            delay = min(delay * 2, max_delay)

    raise last_exception


def build_drive_service():
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build("drive", "v3", credentials=creds)
        print("Google Drive service initialized successfully.")
        return service
    except Exception as e:
        print(f"Error initializing Google Drive service: {type(e).__name__}: {e}")
        raise SystemExit(1)


def build_x_clients():
    try:
        client_v2 = tweepy.Client(
            consumer_key=CONSUMER_KEY,
            consumer_secret=CONSUMER_SECRET,
            access_token=ACCESS_KEY,
            access_token_secret=ACCESS_SECRET,
        )

        auth = tweepy.OAuth1UserHandler(CONSUMER_KEY, CONSUMER_SECRET)
        auth.set_access_token(ACCESS_KEY, ACCESS_SECRET)

        api = tweepy.API(auth, wait_on_rate_limit=True)
        print("Twitter/X API initialized successfully.")
        return api, client_v2
    except Exception as e:
        print(f"Error initializing Twitter/X API: {type(e).__name__}: {e}")
        raise SystemExit(1)


def verify_x_access(api, client_v2):
    """Performs lightweight auth checks for both v1.1 and v2 clients."""
    try:
        print("Verifying v1.1 credentials...")
        user = retry_call(
            api.verify_credentials,
            max_retries=3,
            initial_delay=3,
            step_name="Verify v1.1 credentials",
        )
        if user:
            print(f"v1.1 auth OK for @{user.screen_name}")
        else:
            print("v1.1 auth check returned no user object.")

        print("Verifying v2 user context...")
        me = retry_call(
            client_v2.get_me,
            user_auth=True,
            max_retries=3,
            initial_delay=3,
            step_name="Verify v2 user context",
        )
        if me and me.data:
            print(f"v2 auth OK for user id {me.data.id}")
        else:
            print("v2 auth check returned no data.")
    except Exception as e:
        print(f"X auth verification failed: {type(e).__name__}: {e}")
        raise


def list_drive_folders(drive_service, parent_id):
    """Lists all subfolders in the given Google Drive folder."""
    query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    folders = []
    page_token = None

    while True:
        results = retry_call(
            drive_service.files().list(
                q=query,
                fields="nextPageToken, files(id, name)",
                pageSize=500,
                pageToken=page_token,
            ).execute,
            step_name="List drive folders",
            max_retries=4,
            initial_delay=3,
            max_delay=20,
        )

        folders.extend(results.get("files", []))
        page_token = results.get("nextPageToken")

        if not page_token:
            break

    print(f"Found {len(folders)} folders in Drive.")
    return folders


def list_drive_files(drive_service, folder_id):
    """Lists all files in a given Google Drive folder."""
    query = f"'{folder_id}' in parents and trashed=false"
    files = []
    page_token = None

    while True:
        results = retry_call(
            drive_service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
            ).execute,
            step_name="List drive files",
            max_retries=4,
            initial_delay=3,
            max_delay=20,
        )
        files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    return files


def pick_files_from_folder(files):
    images = [f for f in files if f["name"].lower().endswith(image_extensions)]
    descriptions = [f for f in files if f["name"].lower().endswith(description_extensions)]

    if not images or not descriptions:
        return None, None

    max_pick = min(MAX_IMAGES_PER_TWEET, len(images))
    min_pick = min(MIN_IMAGES_PER_TWEET, max_pick)
    image_count = random.randint(min_pick, max_pick)
    selected_images = random.sample(images, image_count)
    descriptions.sort(key=lambda f: (0 if f["name"].lower().endswith(".txt") else 1, f["name"].lower()))
    description = descriptions[0]
    return selected_images, description


def select_valid_drive_folder(drive_service, folders, history):
    """Selects a random folder that contains at least one image and one supported description file."""
    now = datetime.now(miami_tz)
    random.shuffle(folders)
    for folder in folders:
        if was_recently_recommended(history, folder["id"], now):
            print(f"Skipping recently recommended folder: {folder['name']}")
            continue

        files = list_drive_files(drive_service, folder["id"])
        image_files, description_file = pick_files_from_folder(files)
        if image_files and description_file:
            print(f"Selected valid folder: {folder['name']}")
            return folder, image_files, description_file

    print("No valid folders found.")
    return None, None, None


def download_file_from_drive(drive_service, file_id, destination_path):
    """Downloads a file from Google Drive, exporting Google Docs files if necessary."""
    try:
        file_metadata = retry_call(
            drive_service.files().get(fileId=file_id, fields="mimeType, name").execute,
            step_name="Get file metadata",
            max_retries=4,
            initial_delay=3,
            max_delay=20,
        )
        file_mime_type = file_metadata.get("mimeType", "")
        file_name = file_metadata.get("name", "")

        export_mime_types = {
            "application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.google-apps.spreadsheet": "text/csv",
            "application/vnd.google-apps.presentation": "application/pdf",
            "application/vnd.google-apps.drawing": "image/png",
        }

        if file_mime_type in export_mime_types:
            export_mime = export_mime_types[file_mime_type]
            request = drive_service.files().export_media(fileId=file_id, mimeType=export_mime)

            extension_map = {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "text/csv": ".csv",
                "application/pdf": ".pdf",
                "image/png": ".png",
            }
            file_extension = extension_map.get(export_mime, ".txt")
            destination_path = os.path.splitext(destination_path)[0] + file_extension
        else:
            request = drive_service.files().get_media(fileId=file_id)

        with io.FileIO(destination_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = retry_call(
                    downloader.next_chunk,
                    step_name="Download chunk",
                    max_retries=4,
                    initial_delay=2,
                    max_delay=20,
                )

        print(f"Downloaded {destination_path}")
        return destination_path

    except Exception as e:
        print(f"Error downloading file {file_id} ({file_name}): {type(e).__name__}: {e}")
        return None


def get_alt_text_from_description(description_file):
    """Extracts first sentence for alt text and returns full description text."""
    try:
        if description_file.lower().endswith(".docx"):
            doc = docx.Document(description_file)
            content = "\n".join([para.text for para in doc.paragraphs]).strip()
        else:
            with open(description_file, "r", encoding="utf-8") as f:
                content = f.read().strip()

        if not content:
            return "Sapphic Recommendation", "No description available."

        alt_text = content.split(".")[0] if "." in content else content[:100]
        alt_text = alt_text.strip()[:1000]
        return alt_text, content
    except Exception as e:
        print(f"Error reading description file {description_file}: {type(e).__name__}: {e}")
        return "Sapphic Recommendation", "No description available."


def wait_for_media_ready(api, media_id, max_checks=12, initial_delay=2):
    delay = initial_delay

    for check_num in range(1, max_checks + 1):
        try:
            print(f"Check media status: attempt {check_num}/{max_checks}")
            status = api.get_media_upload_status(media_id)
            processing_info = getattr(status, "processing_info", None)

            if not processing_info:
                print("Media status: ready (no processing_info returned)")
                return True

            state = processing_info.get("state")
            print(f"Media processing state: {state}")

            if state == "succeeded":
                print("Media processing complete.")
                return True

            if state == "failed":
                error_info = processing_info.get("error", {})
                raise RuntimeError(f"Media processing failed: {error_info}")

            check_after_secs = processing_info.get("check_after_secs", delay)
            print(f"Media still processing; waiting {check_after_secs} seconds...")
            time.sleep(check_after_secs)
            delay = min(delay * 2, 30)

        except tweepy.TweepyException as e:
            print(f"Media status check issue: {type(e).__name__}: {e}")
            if is_transient_error(e):
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue

            print("Proceeding without media status polling.")
            return True
        except Exception as e:
            print(f"Unexpected media status issue: {type(e).__name__}: {e}")
            raise

    raise TimeoutError("Media was not ready before timeout.")


def tweet_image_and_reply(api, client_v2, image_paths, description_path):
    alt_text, full_text = get_alt_text_from_description(description_path)

    try:
        media_ids = []
        for index, image_path in enumerate(image_paths, start=1):
            print(f"Selected image {index}/{len(image_paths)}: {image_path}")
            media = retry_call(
                api.media_upload,
                image_path,
                media_category="tweet_image",
                max_retries=5,
                initial_delay=5,
                max_delay=90,
                step_name=f"Media upload {index}",
            )

            wait_for_media_ready(api, media.media_id)
            media_ids.append(media.media_id)

            retry_call(
                api.create_media_metadata,
                media.media_id,
                alt_text,
                max_retries=5,
                initial_delay=3,
                max_delay=30,
                step_name=f"Media metadata {index}",
            )

        time.sleep(2)

        tweet = retry_call(
            client_v2.create_tweet,
            text="₊ ⊹ ❤︎ sapphic recommendations ❤︎ ⊹ ₊",
            media_ids=media_ids,
            user_auth=True,
            max_retries=5,
            initial_delay=8,
            max_delay=120,
            step_name="Create main tweet",
        )

        print(f"Tweeted: {alt_text}")

        if full_text.strip():
            time.sleep(2)
            retry_call(
                client_v2.create_tweet,
                text=full_text,
                in_reply_to_tweet_id=tweet.data["id"],
                user_auth=True,
                max_retries=5,
                initial_delay=8,
                max_delay=120,
                step_name="Create reply tweet",
            )
            print("Replied with full description.")

    except Exception as e:
        print(f"Error tweeting: {type(e).__name__}: {e}")
        return False

    return True


def reset_temp_folder(path):
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)


def tweet_random_images(drive_service, api, client_v2):
    history = load_recommendation_history()
    folders = list_drive_folders(drive_service, DRIVE_FOLDER_ID)
    valid_folder, image_files, description_file = select_valid_drive_folder(drive_service, folders, history)

    if not valid_folder:
        return False

    run_stamp = datetime.now(miami_tz).strftime("%Y%m%d_%H%M%S")
    folder_name = f"{valid_folder['name']}_{run_stamp}".replace("/", "_")
    run_folder = os.path.join(local_base_folder, folder_name)
    os.makedirs(run_folder, exist_ok=True)

    local_images = []
    for image_file in image_files:
        local_image = download_file_from_drive(
            drive_service, image_file["id"], os.path.join(run_folder, image_file["name"])
        )
        if local_image:
            local_images.append(local_image)

    local_description = download_file_from_drive(
        drive_service, description_file["id"], os.path.join(run_folder, description_file["name"])
    )

    if not local_images or not local_description:
        print("Could not download required files.")
        return False

    success = tweet_image_and_reply(api, client_v2, local_images, local_description)
    if success:
        mark_recommended(history, valid_folder)
    return success


def main():
    validate_environment()
    reset_temp_folder(local_base_folder)
    drive_service = build_drive_service()
    api, client_v2 = build_x_clients()

    verify_x_access(api, client_v2)
    success = tweet_random_images(drive_service, api, client_v2)

    if success:
        print("Bot execution completed successfully.")
    else:
        print("Bot execution completed without posting.")


if __name__ == "__main__":
    main()
