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

def list_drive_folders(parent_id):
    """Lists all subfolders in the given Google Drive folder."""
    query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    folders = []
    page_token = None

    while True:
        results = drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name)",
            pageSize=500,  # Increase page size to retrieve more results per request
            pageToken=page_token
        ).execute()

        folders.extend(results.get('files', []))
        page_token = results.get('nextPageToken')

        if not page_token:
            break  # Exit loop when there are no more pages

    print(f"Found {len(folders)} folders in Drive.")
    return folders

def list_drive_files(folder_id):
    """Lists all files in a given Google Drive folder."""
    query = f"'{folder_id}' in parents and trashed=false"
    files = []
    page_token = None
    while True:
        results = drive_service.files().list(q=query, fields="files(id, name, mimeType)", pageToken=page_token).execute()
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

        # Define export formats for Google Docs types
        export_mime_types = {
            'application/vnd.google-apps.document': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # Export to .docx
            'application/vnd.google-apps.spreadsheet': 'text/csv',  # Export to .csv
            'application/vnd.google-apps.presentation': 'application/pdf',  # Export to .pdf
            'application/vnd.google-apps.drawing': 'image/png'  # Export to .png
        }

        if file_mime_type in export_mime_types:
            export_mime = export_mime_types[file_mime_type]
            request = drive_service.files().export_media(fileId=file_id, mimeType=export_mime)

            # Assign correct file extension based on export format
            extension_map = {
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
                'text/csv': '.csv',
                'application/pdf': '.pdf',
                'image/png': '.png'
            }
            file_extension = extension_map.get(export_mime, '.txt')  # Default to .txt if unknown
            destination_path = os.path.splitext(destination_path)[0] + file_extension

        else:
            # Normal file download (for .rtf, .txt, .doc, .docx)
            request = drive_service.files().get_media(fileId=file_id)

        with io.FileIO(destination_path, 'wb') as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        print(f"Downloaded {destination_path}")

    except Exception as e:
        print(f"Error downloading file {file_id} ({file_name}): {e}")

def get_alt_text_from_description(description_file):
    """Extracts the first sentence from a description file for alt text and returns the full text."""
    try:
        with open(description_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return "Sapphic Recommendation", "No description available."
            
            # Use the first 100 characters or first sentence as alt text
            alt_text = content.split('.')[0] if '.' in content else content[:100]
            return alt_text.strip(), content
    except Exception as e:
        print(f"Error reading description file {description_file}: {e}")
        return "Sapphic Recommendation", "No description available."

def tweet_images_from_folder(folder_path):
    """Tweets a random image from the specified folder."""
    images = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if f.lower().endswith(supported_formats)]
    descriptions = [os.path.join(folder_path, f) for f in os.listdir(folder_path) if f.lower().endswith(supported_text_extensions)]
    if not images or not descriptions:
        print(f"No images or description file found in folder: {folder_path}")
        return False
    selected_image = random.choice(images)
    alt_text, full_text = get_alt_text_from_description(descriptions[0])
    try:
        media = api.media_upload(selected_image)
        api.create_media_metadata(media.media_id, alt_text)
        client_v2.create_tweet(text="₊ ⊹ ❤︎ sapphic recommendations ❤︎ ⊹ ₊", media_ids=[media.media_id])
        print(f"Tweeted {alt_text}")
    except Exception as e:
        print(f"Error tweeting: {e}")
        return False
    return True

def tweet_random_images():
    """Selects a valid Drive folder and tweets an image."""
    folders = list_drive_folders(DRIVE_FOLDER_ID)
    valid_folder = select_valid_drive_folder(folders)
    if valid_folder:
        local_folder = download_drive_folder(valid_folder['id'], os.path.join(local_base_folder, valid_folder['name']))
        tweet_images_from_folder(local_folder)

def main():
    tweet_random_images()

if __name__ == "__main__":
    main()
