# AspectRatioSorter
This script sorts image and video files in the landscape and portrait subfolders in the original folder.

## How to use
1. Download the script and save it in the folder containing the files you want to sort.
2. Run the script by double-clicking on it or running it through the command line (e.g. ```python AspectRatioSorter.py```)
3. Follow the prompts to enter the path of the folder containing the files. (e.g. ```C:\Users\Username\Documents\MediaFolder```)
4. Wait for the script to run and the files to be sorted into the appropriate subfolders.

## Requirements
- Python 3
- OpenCV (```pip install opencv-python```)
- tqdm (```pip install tqdm```)

## Notes
- The script only sorts files with the extensions .mp4, .mov, .jpg, .jpeg, .png
- The script will not sort files in subfolders within the target folder.
- The script will not overwrite files in the subfolders.
- If a file is not landscape or portrait it will not be moved.
- The script will not work on the file system of a Linux or macOS.

## How the script works
The script starts by creating the subfolders where the files will be sorted. Then it lists all the files in the directory and exclude directories. Then it creates a progress bar using tqdm and iterates through all the files in the directory. Then it reads the image file or open the video file and gets the width and height of the image or video. Then it calculates the aspect ratio of the image or video and determine if the image/video is portrait or landscape. Then it creates the subfolder if it doesn't exist and move the file to the appropriate subfolder. Finally, it updates the progress bar
