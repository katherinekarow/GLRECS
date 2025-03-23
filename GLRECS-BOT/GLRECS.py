import os
import random
import tweepy
from time import sleep
from datetime import datetime
import pytz
from dotenv import load_dotenv
import io

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
print(f"CONSUMER_KEY: {CONSUMER_KEY}")
print(f"CONSUMER_SECRET: {CONSUMER_SECRET}")
print(f"ACCESS_KEY: {ACCESS_KEY}")
print(f"ACCESS_SECRET: {ACCESS_SECRET}")

# Google Drive configuration
DRIVE_FOLDER_ID = os.getenv('DRIVE_FOLDER_ID') 
SERVICE_ACCOUNT_FILE = os.getenv('SERVICE_ACCOUNT_FILE') 
SCOPES = ['https://www.googleapis.com/auth/drive']

# Debug: Print Google Drive configuration
print(f"DRIVE_FOLDER_ID: {DRIVE_FOLDER_ID}")
print(f"SERVICE_ACCOUNT_FILE: {SERVICE_ACCOUNT_FILE}")

# Ensure the base temporary directory exists
local_base_folder = './GLRECS_temp'
os.makedirs(local_base_folder, exist_ok=True)

# Initialize Google Drive service
try:
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=creds)
    print("Google Drive service initialized successfully.")
except Exception as e:
    print(f"Error initializing Google Drive service: {e}")
    exit(1)

# Initialize Tweepy (Twitter API)
try:
    client_v2 = tweepy.Client(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        access_token=ACCESS_KEY,
        access_token_secret=ACCESS_SECRET
    )
    auth = tweepy.OAuth1UserHandler(CONSUMER_KEY, CONSUMER_SECRET)
    auth.set_access_token(ACCESS_KEY, ACCESS_SECRET)
    api = tweepy.API(auth)
    print("Twitter API initialized successfully.")
except Exception as e:
    print(f"Error initializing Twitter API: {e}")
    exit(1)

# --- Configuration ---
# Comprehensive list of supported image formats
supported_formats = (
    '.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp',
    '.tiff', '.svg', '.heif', '.ico', '.raw', '.jfif',
    '.exif', '.dng', '.mp4', '.avi', '.mov', '.wmv', '.mkv',
    '.flv', '.webm', '.gifv', '.mp3', '.wav', '.aac', '.ogg'
)

# Comprehensive list of supported text file extensions
supported_text_extensions = (
    '.txt', '.rtf', '.doc', '.docx', '.pdf', '.odt',
    '.markdown', '.csv', '.html', '.xml', '.json'
)

# Miami timezone
miami_tz = pytz.timezone('America/New_York')

# --- Google Drive Helper Functions ---

def list_drive_folders(parent_id):
    """Lists all subfolders in the given Google Drive folder using pagination."""
    folders = []
    page_token = None

    while True:
        query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = drive_service.files().list(
            q=query,
            fields="files(id, name)",
            pageToken=page_token
        ).execute()

        folders.extend(results.get('files', []))
        page_token = results.get('nextPageToken', None)

        if page_token is None:
            break  # No more pages

    print(f"Found {len(folders)} folders in Drive.")
    return folders

def list_drive_files(folder_id):
    """Lists files in a given Google Drive folder."""
    query = f"'{folder_id}' in parents and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    return results.get('files', [])

def download_file_from_drive(file_id, destination_path):
    """Downloads a file from Google Drive to a local destination."""
    try:
        # First, check the file metadata to determine its type
        request = drive_service.files().get_media(fileId=file_id)
        with io.FileIO(destination_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
        print(f"Downloaded {destination_path}")
    except Exception as e:
        print(f"Error downloading file {file_id}: {e}")

# --- Tweeting Functions ---

def get_alt_text_from_description(file_path):
    """Reads the first two lines from a description file to create alt text and returns full text."""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            alt_text = "".join(lines[:2]).strip()  # Use first two lines as alt text
            full_text = "".join(lines).strip()      # Full text for follow-up tweet
            print(f"Read alt text: {alt_text}")
            return alt_text, full_text
    except Exception as e:
        print(f"Error reading description file {file_path}: {e}")
        return None, None

def tweet_images_from_folder(folder_path, selected_image, description_file):
    """Tweets the selected image from the specified local folder along with the description."""
    if not selected_image or not description_file:
        print("Invalid image or description file.")
        return False

    alt_text, full_text = get_alt_text_from_description(description_file)
    if not alt_text or not full_text:
        print("No valid alt text or full text found.")
        return False

    media_ids = []
    try:
        media = api.media_upload(selected_image)
        api.create_media_metadata(media.media_id, alt_text)
        media_ids.append(media.media_id)
        print(f"Uploaded media with ID: {media.media_id}")
    except tweepy.errors.TooManyRequests:
        print("Rate limit hit, sleeping for 4 hours...")
        sleep(6 * 60 * 60)
        return False
    except Exception as e:
        print(f"Error uploading image {selected_image}: {e}")
        return False

    if media_ids:
        try:
            tweet_text = "₊ ⊹ ❤︎ sapphic recommendations ❤︎ ⊹ ₊"
            response = client_v2.create_tweet(text=tweet_text, media_ids=media_ids)
            client_v2.create_tweet(text=full_text, in_reply_to_tweet_id=response.data['id'])
            current_time = datetime.now(miami_tz).strftime('%Y-%m-%d %I:%M %p')
            print(f"Rec Tweeted: {alt_text} at {current_time}")
        except Exception as e:
            print(f"Error tweeting text: {e}")
            return False

    return True

def tweet_random_images():
    """
    Randomly selects a series folder from the Google Drive folder (GLRECS),
    checks for valid images and description files based on extensions,
    and tweets if conditions are met.
    """
    if not DRIVE_FOLDER_ID:
        print("No DRIVE_FOLDER_ID provided.")
        return

    drive_folders = list_drive_folders(DRIVE_FOLDER_ID)
    if not drive_folders:
        print("No eligible folders found on Google Drive.")
        return

    while True:
        folder = random.choice(drive_folders)
        print(f"Selected Drive folder: {folder['name']} (ID: {folder['id']})")
        files = list_drive_files(folder['id'])

        images = []
        description_file = None

        # Check for valid images and description files using extensions
        for f in files:
            file_name = f['name']
            lower = file_name.lower()

            # Check file extensions directly
            if any(lower.endswith(ext) for ext in supported_formats):
                images.append(f)
            elif any(lower.startswith(prefix) and lower.endswith(ext) for prefix in ['desc'] for ext in supported_text_extensions):
                description_file = f

        # If both valid image and description are found
        if images and description_file:
            print(f"Found {len(images)} images and 1 description file.")
            selected_image_file = random.choice(images)
            selected_image_path = os.path.join(local_base_folder, selected_image_file['name'])
            
            # Download only the selected image and description file
            download_file_from_drive(selected_image_file['id'], selected_image_path)
            description_path = os.path.join(local_base_folder, description_file['name'])
            download_file_from_drive(description_file['id'], description_path)

            # Now, tweet the image with the description
            success = tweet_images_from_folder(local_base_folder, selected_image_path, description_path)
            if success:
                break
        else:
            print("No valid images or description file found. Retrying with another folder...")

def main():
    tweet_random_images()

if __name__ == "__main__":
    main()
