import os
import random
import time
import io
from datetime import datetime

import tweepy
import pytz
import docx
from dotenv import load_dotenv

# Google API imports
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.service_account import Credentials

# Load environment variables
load_dotenv()
CONSUMER_KEY = os.getenv('CONSUMER_KEY')
CONSUMER_SECRET = os.getenv('CONSUMER_SECRET')
ACCESS_KEY = os.getenv('ACCESS_KEY')
ACCESS_SECRET = os.getenv('ACCESS_SECRET')

# Debug: Print environment variables
print("Loaded environment variables:")
print(f"CONSUMER_KEY: {'***' if CONSUMER_KEY else 'MISSING'}")
print(f"CONSUMER_SECRET: {'***' if CONSUMER_SECRET else 'MISSING'}")
print(f"ACCESS_KEY: {'***' if ACCESS_KEY else 'MISSING'}")
print(f"ACCESS_SECRET: {'***' if ACCESS_SECRET else 'MISSING'}")

# Google Drive configuration
DRIVE_FOLDER_ID = os.getenv('DRIVE_FOLDER_ID')
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE')
SCOPES = ['https://www.googleapis.com/auth/drive']

# Debug: Print Google Drive configuration
print(f"DRIVE_FOLDER_ID: {'***' if DRIVE_FOLDER_ID else 'MISSING'}")
print(f"SERVICE_ACCOUNT_FILE: {SERVICE_ACCOUNT_FILE}")

# Initialize Google Drive service
try:
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=creds)
    print("Google Drive service initialized successfully.")
except Exception as e:
    print(f"Error initializing Google Drive service: {type(e).__name__}: {e}")
    raise SystemExit(1)

# Initialize Tweepy (Twitter/X API)
try:
    client_v2 = tweepy.Client(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        access_token=ACCESS_KEY,
        access_token_secret=ACCESS_SECRET
    )

    auth = tweepy.OAuth1UserHandler(CONSUMER_KEY, CONSUMER_SECRET)
    auth.set_access_token(ACCESS_KEY, ACCESS_SECRET)

    api = tweepy.API(auth, wait_on_rate_limit=True)
    print("Twitter/X API initialized successfully.")
except Exception as e:
    print(f"Error initializing Twitter/X API: {type(e).__name__}: {e}")
    raise SystemExit(1)

# Configuration
local_base_folder = './GLRECS_temp'
supported_formats = (
    '.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp',
    '.tiff', '.svg', '.heif', '.ico', '.raw', '.jfif',
    '.exif', '.dng', '.weep', '.mp4', '.avi', '.mov', '.wmv',
    '.mkv', '.flv', '.webm', '.gifv',
    '.mp3', '.wav', '.aac', '.ogg'
)
supported_text_extensions = (
    '.txt', '.rtf', '.doc', '.docx', '.pdf', '.odt', '.markdown',
    '.csv', '.html', '.xml', '.json'
)
miami_tz = pytz.timezone('America/New_York')


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
        "over capacity"
    ]
    return any(marker in msg for marker in transient_markers)


def retry_call(fn, *args, max_retries=5, initial_delay=5, max_delay=90, step_name="API call", **kwargs):
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


def verify_x_access():
    """Performs lightweight auth checks for both v1.1 and v2 clients."""
    try:
        print("Verifying v1.1 credentials...")
        user = retry_call(
            api.verify_credentials,
            max_retries=3,
            initial_delay=3,
            step_name="Verify v1.1 credentials"
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
            step_name="Verify v2 user context"
        )
        if me and me.data:
            print(f"v2 auth OK for user id {me.data.id}")
        else:
            print("v2 auth check returned no data.")
    except Exception as e:
        print(f"X auth verification failed: {type(e).__name__}: {e}")
        raise


def list_drive_folders(parent_id):
    """Lists all subfolders in the given Google Drive folder."""
    query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    folders = []
    page_token = None

    while True:
        results = drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name)",
            pageSize=500,
            pageToken=page_token
        ).execute()

        folders.extend(results.get('files', []))
        page_token = results.get('nextPageToken')

        if not page_token:
            break

    print(f"Found {len(folders)} folders in Drive.")
    return folders


def list_drive_files(folder_id):
    """Lists all files in a given Google Drive folder."""
    query = f"'{folder_id}' in parents and trashed=false"
    files = []
    page_token = None

    while True:
        results = drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token
        ).execute()
        files.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break

    return files


def select_valid_drive_folder(folders):
    """Selects a random folder that contains at least one image and one text file."""
    random.shuffle(folders)
    for folder in folders:
        files = list_drive_files(folder['id'])
        has_image = any(f['name'].lower().endswith(supported_formats) for f in files)
        has_text = any(f['name'].lower().endswith(supported_text_extensions) for f in files)
        if has_image and has_text:
            print(f"Selected valid folder: {folder['name']}")
            return folder

    print("No valid folders found.")
    return None


def download_drive_folder(folder_id, destination_folder):
    """Downloads all files in a Google Drive folder to a local folder."""
    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)

    files = list_drive_files(folder_id)
    for file in files:
        file_path = os.path.join(destination_folder, file['name'])
        download_file_from_drive(file['id'], file_path)

    return destination_folder


def download_file_from_drive(file_id, destination_path):
    """Downloads a file from Google Drive, exporting Google Docs files if necessary."""
    try:
        file_metadata = drive_service.files().get(fileId=file_id, fields="mimeType, name").execute()
        file_mime_type = file_metadata.get('mimeType', '')
        file_name = file_metadata.get('name', '')

        export_mime_types = {
            'application/vnd.google-apps.document': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.google-apps.spreadsheet': 'text/csv',
            'application/vnd.google-apps.presentation': 'application/pdf',
            'application/vnd.google-apps.drawing': 'image/png'
        }

        if file_mime_type in export_mime_types:
            export_mime = export_mime_types[file_mime_type]
            request = drive_service.files().export_media(fileId=file_id, mimeType=export_mime)

            extension_map = {
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
                'text/csv': '.csv',
                'application/pdf': '.pdf',
                'image/png': '.png'
            }
            file_extension = extension_map.get(export_mime, '.txt')
            destination_path = os.path.splitext(destination_path)[0] + file_extension
        else:
            request = drive_service.files().get_media(fileId=file_id)

        with io.FileIO(destination_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        print(f"Downloaded {destination_path}")

    except Exception as e:
        print(f"Error downloading file {file_id} ({file_name}): {type(e).__name__}: {e}")


def get_alt_text_from_description(description_file):
    """Extracts the first sentence from a description file for alt text and returns the full text."""
    try:
        if description_file.lower().endswith('.docx'):
            doc = docx.Document(description_file)
            content = '\n'.join([para.text for para in doc.paragraphs]).strip()
        else:
            with open(description_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()

        if not content:
            return "Sapphic Recommendation", "No description available."

        alt_text = content.split('.')[0] if '.' in content else content[:100]
        alt_text = alt_text.strip()[:1000]  # Safe cap for alt text
        return alt_text, content
    except Exception as e:
        print(f"Error reading description file {description_file}: {type(e).__name__}: {e}")
        return "Sapphic Recommendation", "No description available."


def wait_for_media_ready(media_id, max_checks=12, initial_delay=2):
    """
    Polls media status when available.
    For simple image uploads this often returns immediately, but this keeps
    the script safe if X reports processing information.
    """
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
            # For simple image upload, some flows may not expose processing info.
            # If status lookup itself is transient, retry; otherwise continue.
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


def tweet_images_from_folder(folder_path):
    """Tweets a random image from the specified folder and replies with the full description."""
    images = [
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if f.lower().endswith(supported_formats)
    ]
    descriptions = [
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if f.lower().endswith(supported_text_extensions)
    ]

    if not images or not descriptions:
        print(f"No images or description file found in folder: {folder_path}")
        return False

    selected_image = random.choice(images)
    alt_text, full_text = get_alt_text_from_description(descriptions[0])

    try:
        print(f"Selected image: {selected_image}")

        print("Uploading media...")
        media = retry_call(
            api.media_upload,
            selected_image,
            media_category="tweet_image",
            max_retries=5,
            initial_delay=5,
            max_delay=90,
            step_name="Media upload"
        )

        wait_for_media_ready(media.media_id)

        print("Adding alt text...")
        retry_call(
            api.create_media_metadata,
            media.media_id,
            alt_text,
            max_retries=5,
            initial_delay=3,
            max_delay=30,
            step_name="Media metadata"
        )

        print("Waiting briefly after metadata...")
        time.sleep(2)

        print("Creating main tweet...")
        tweet = retry_call(
            client_v2.create_tweet,
            text="₊ ⊹ ❤︎ sapphic recommendations ❤︎ ⊹ ₊",
            media_ids=[media.media_id],
            user_auth=True,
            max_retries=5,
            initial_delay=8,
            max_delay=120,
            step_name="Create main tweet"
        )

        print(f"Tweeted: {alt_text}")

        if full_text.strip():
            print("Waiting briefly before reply...")
            time.sleep(2)

            print("Creating reply tweet...")
            retry_call(
                client_v2.create_tweet,
                text=full_text,
                in_reply_to_tweet_id=tweet.data['id'],
                user_auth=True,
                max_retries=5,
                initial_delay=8,
                max_delay=120,
                step_name="Create reply tweet"
            )
            print("Replied with full description.")

    except Exception as e:
        print(f"Error tweeting: {type(e).__name__}: {e}")
        return False

    return True


def tweet_random_images():
    """Selects a valid Drive folder and tweets an image."""
    folders = list_drive_folders(DRIVE_FOLDER_ID)
    valid_folder = select_valid_drive_folder(folders)

    if valid_folder:
        local_folder = download_drive_folder(
            valid_folder['id'],
            os.path.join(local_base_folder, valid_folder['name'])
        )
        return tweet_images_from_folder(local_folder)

    return False


def main():
    verify_x_access()
    success = tweet_random_images()
    if success:
        print("Bot execution completed successfully.")
    else:
        print("Bot execution completed without posting.")


if __name__ == "__main__":
    main()