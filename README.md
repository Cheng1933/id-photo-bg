# ID Photo Background Replacer

**A small, fully offline Windows desktop app that turns an ordinary photo into an
ID photo with a solid blue / white / red background.**

[中文说明 / Chinese README](README.zh-CN.md)

![screenshot](docs/screenshot.png)

---

## Why this exists

A few friends needed "one-inch blue-background" photos for job applications and
had no Photoshop skills. Every web service that does this wants an upload, and
every uploaded ID photo is a privacy leak.

So I built a desktop tool instead. It runs entirely on the local machine, never
opens a network connection, and produces a print-ready JPEG at 300 dpi.

**A note on how this was built:** I am not a professional developer. This project
was made with heavy AI assistance — I described what I wanted and worked through
the bugs one by one. It is not beautifully architected. But the pitfalls section
below is real, hard-won, and probably the most useful part of this repository if
you are building something similar.

## What it does

* **Drag and drop** a photo, or an entire folder, onto the window.
* **Automatic matting** — person cut out from any background, hair details kept.
* **Face detection + auto crop** to a chosen ID-photo specification.
* **Solid colour background**: 7 presets plus a full custom picker.
* **Custom colour picker** with a saturation/value field, a hue bar, and a
  **screen eyedropper** — pick a colour from anywhere on screen (a browser, a
  chat window, anything).
* **Specs table** built in: one-inch, two-inch, passport, visa, driver's licence,
  social-security card, marriage certificate, and more (China + international).
* **File-size targeting** — some application systems demand "under 25 KB".
  Enter a minimum and/or maximum and it searches JPEG quality to fit.
* **Export several specs at once** — the same photo as one-inch blue *and*
  two-inch red in a single run; matting is computed only once.
* **6-inch photo paper layout** — arranges as many copies as fit on a 152×102 mm
  sheet, ready to send to a print shop.
* **Bring your own model** — add any ONNX matting model through the UI.
* **Transparent PNG** output, for people who want to do the rest themselves.

## Quick start

### Option A — use the prebuilt .exe (no Python needed)

1. Download `证件照换底.zip` from Releases.
2. Unzip it **anywhere, including a folder with a Chinese name**.
3. Run `证件照换底.exe`.

Windows SmartScreen will warn you the first time (the exe is unsigned, and
code-signing certificates cost money). Click *More info* then *Run anyway*.
Everything after that is offline.

### Option B — run from source

```powershell
git clone https://github.com/Cheng1933/id-photo-bg.git
cd id-photo-bg

# 1. fetch the upstream matting kernel and apply our patch
powershell -ExecutionPolicy Bypass -File scripts\setup_vendor.ps1

# 2. install the Python dependencies
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 3. download the model weights (~114 MB, published as release assets)
powershell -ExecutionPolicy Bypass -File scripts\download_models.ps1

# 4. run
.venv\Scripts\python run_app.py
```

There is also a small CLI for scripting:

```powershell
python run_app.py photo.jpg out\            # crop to the default spec, blue background
python run_app.py photo.jpg out\ --bgonly   # keep framing, only replace the background
```

## How it works

The heavy lifting is done by [HivisionIDPhotos](https://github.com/Zeyi-Lin/HivisionIDPhotos)
(Apache-2.0), which handles matting, face detection and ID-photo cropping. This
project is a desktop front end plus a set of fixes and additions:

| Layer | What this project adds |
|---|---|
| GUI | CustomTkinter, iOS-flat styling, drag and drop, thumbnail walls, export list |
| Specs | An independently maintained specification table (the upstream CSV has 3 wrong entries) |
| Export | JPEG quality search for a target file-size window; 6-inch paper layout |
| Colour | Saturation/value field + hue bar + screen eyedropper (GDI GetPixel) |
| Models | Add your own ONNX model from the UI, with type auto-detection |
| Packaging | PyInstaller onedir build, ~290 MB unpacked / ~130 MB zipped |

**Models.** Two matting models are used: BiRefNet (int8-quantized, 89 MB) for
quality and MODNet (25 MB) for speed. Details and licences are in
[models/README.md](models/README.md).

## Things that bit me

If you are building a similar tool, these are the parts that cost me the most
time. Every one of them was found by a real bug, not by reading docs.

| Symptom | Root cause | Fix |
|---|---|---|
| Packaged exe crashes on drag and drop, but works from source | The code hooked the Win32 window procedure itself. A self-test that only sent WM_DROPFILES passed — real dragging floods the window proc with WM_NCHITTEST and friends, which all had to go through my hand-written procedure | Use tkinterdnd2 (a Tcl extension) instead of touching the window procedure |
| TclError: image "pyimageN" doesn't exist | Tk widgets store only the *name* of an image, not the object. Releasing the CTkImage destroyed the underlying PhotoImage while widgets still referenced the name | Detach the Tk-level reference first, *then* release the Python object |
| "Sometimes the + button works, sometimes not" | widget.bind() in CustomTkinter only forwards to the internal canvas. A CTkLabel draws its text on a separate inner label layer, which must be bound separately | A bind_click() helper that binds both layers |
| Clicking the eyedropper froze the whole app; it had to be killed from Task Manager | A modal grab_set() was still active when the dialog was hidden, so input went to an invisible window. The follow-cursor preview meanwhile died from a NameError inside an after callback, breaking the timer chain | Release the grab *before* hiding; wrap the callback in try/except with a safe exit; add a 60-second safety net |
| The app started, but no photo could be selected | The "choose photo" action was bound to a segmented button whose value was already the default, so the callback never fired | A dedicated button; segmented buttons only switch modes |
| NoneType object is not subscriptable at startup, only when unzipped into a Chinese-named folder | cv2.imread returns None for non-ASCII paths, and it does not raise | Read bytes with np.fromfile then cv2.imdecode for every image loaded during import |
| Face cropping silently produced nonsense | The face field is *always* a dict, so a "if it is None" guard never triggered and detection never ran | Check that the dict is empty or has no "rectangle" key |
| Clicking "Generate" did nothing at all | A field was renamed in a dataclass and one caller was missed. The exception was raised on the main thread inside a Tk callback, where Tk swallows it silently — and a windowed build has no console | Wrap the UI entry point in try/except and show an error dialog; test by actually pressing the button, with report_callback_exception installed |
| pip install pulled in a 600 MB dependency | The upstream package imports gradio at module level in two files, purely for its own web demo | Remove those module-level imports in our patch |

Three of the bugs above share a shape worth internalising: **my test exercised a
different code path than the user did.** Sending one drag message instead of a
real drag; calling the eyedropper's start and stop helpers without letting its
timer actually tick; calling the engine directly instead of pressing the button.
If you take one thing from this repo, take that.

## A note on ID-photo specifications

There is no single authoritative table of "standard" ID photo sizes, and several
widely copied ones are wrong:

* **China has no single national table.** ID and social-security card photos follow
  GA461-2004 (26×32 mm, 358×441 px **at 350 dpi**, pure white); passports are
  33×48 mm; visas are usually 35×45 mm; the US visa is 600×600 px.
* **The "standard blue" `#438EDB` that circulates online has no official source.**
  The only blue with an official RGB definition is the Ministry of Education's
  bright blue `#64C5FF` for academic-image collection. Both are offered as presets.
* **Do not copy the upstream `size_list_CN.csv` verbatim.** It lists "two-inch" as
  35×53 mm (duplicating "large two-inch"), its social-security entry is actually a
  350-dpi spec, and its Japan visa size is wrong. This project maintains its own
  table in `app/specs.py`.
* File-size limits for online submission cluster around 20–200 KB JPEG, with
  20–40 KB for academic-image collection.

## Project layout

```
app/            the application
  engine.py     matting, face detection, spec cropping, JPEG size targeting
  specs.py      ID-photo specification table (independently maintained)
  layout.py     6-inch photo paper layout
  ui.py         CustomTkinter interface
  main.py       entry point + self-check + CLI
build/          PyInstaller spec and build script
patches/        our changes to the upstream kernel
scripts/        setup_vendor.ps1, download_models.ps1
models/         model weights go here (not in git)
docs/           screenshots and notes
```

## Building the standalone exe

```powershell
powershell -ExecutionPolicy Bypass -File build\build.ps1
```

Produces `dist/证件照换底/` plus a zip. The script also strips things the build
does not need: OpenCV's ffmpeg backend (−27 MB), AVIF support (−7.5 MB), the
unused Tcl9 build of tkdnd, and an entire network stack that this offline tool
never uses (−10 MB).

## Why CPU, and not the GPU

The target machines generally have an NVIDIA card but **no CUDA toolkit**, and
shipping the CUDA runtime plus cuDNN would add roughly 1.8 GB. DirectML was
evaluated too, but ONNX Runtime's DirectML backend failed to build a fused node
(`DmlFusedNode_15_51`) for BiRefNet. On CPU the quality tier takes about 5 s and
the fast tier about 0.2 s per photo, which is fine for this use case.

## License

MIT for the code in this repository. Model weights and the upstream kernel keep
their own licences — **see [THIRD_PARTY.md](THIRD_PARTY.md)**.

In particular: **BRIA RMBG-1.4 and RMBG-2.0 are non-commercial only.** This
project deliberately does not use them, even though `rembg` made RMBG-2.0 its
default in v2.0.80.

Thanks to Zeyi-Lin for HivisionIDPhotos, ZhengPeng7 for BiRefNet, and
TomSchimansky for CustomTkinter.
