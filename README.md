# AspectRatioSorter

![Last Commit](https://img.shields.io/github/last-commit/Ch4r0ne/AspectRatioSorter?style=flat-square)
![Release](https://img.shields.io/github/v/release/Ch4r0ne/AspectRatioSorter?style=flat-square)
![License](https://img.shields.io/github/license/Ch4r0ne/AspectRatioSorter?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10%2B-2b2b2b?style=flat-square)
![PyQt6](https://img.shields.io/badge/PyQt6-GUI-2b2b2b?style=flat-square)
![OpenCV](https://img.shields.io/badge/OpenCV-cv2-2b2b2b?style=flat-square)

Business-style **PyQt6** app that sorts media by aspect ratio into **portrait** / **landscape** folders.

![Preview](./Preview/AspectRatioSorter.png)

## What it does
- Supported: **.jpg .jpeg .png .mp4 .mov**
- Rule: `width / height < 1` → `portrait`, else → `landscape`
- Workflow: **Analyze → Sort (MOVE)**
- Output behavior:
  - Output empty → creates `portrait/` + `landscape/` directly in **Source**
  - Output set → creates `Source/<Output>/portrait|landscape`

## Package (single EXE)
```powershell
pyinstaller -y --clean --onefile --noconsole --name "AspectRatioSorter" `
  --collect-all "cv2" `
  "AspectRatioSorter.py"
