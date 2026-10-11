# Third-party components / 第三方组件

This project stands on other people's work. / 本项目建立在别人的成果之上。

**This repository ships no third-party source code and no model weights.**
It contains only a patch file for the upstream kernel, plus a script that
fetches the upstream and applies the patch.

**本仓库不分发任何第三方源码，也不分发模型权重。** 只包含一份针对上游内核的补丁，
以及一个「拉取上游 + 打补丁」的脚本。

---

## 1. Image matting kernel / 抠图内核

| Project | License | How it is used here |
|---|---|---|
| [HivisionIDPhotos](https://github.com/Zeyi-Lin/HivisionIDPhotos) | **Apache-2.0** | Fetched by `scripts/setup_vendor.ps1`; our 4 small patches live in `patches/`. It performs matting, face detection and ID-photo cropping. |

We do **not** vendor its code. If you fork this project, fetch the upstream
yourself and apply `patches/hivision-idphotos.patch`. The patch baseline commit
is recorded in `patches/UPSTREAM_COMMIT.txt`.

我们**不**把它的代码放进本仓库。请自行拉取上游再打补丁，补丁基准 commit 记在
`patches/UPSTREAM_COMMIT.txt`。

### What our patches change / 我们的补丁改了什么

1. `plugin/beauty/grind_skin.py` — remove a module-level `import gradio` (it made the
   whole package unimportable unless a several-hundred-MB web UI library was installed).
2. `plugin/beauty/whitening.py` — same `gradio` removal, **and** read the LUT image via
   `np.fromfile` + `cv2.imdecode`. The original `cv2.imread` silently returns `None` for
   non-ASCII paths, so the program crashed at startup whenever it was unzipped into a
   folder with a Chinese name — which is the normal case for Chinese users.
3. `plugin/template/template_calculator.py` — the same non-ASCII path fix, for the
   layout template.
4. `creator/face_detector.py` — make the module-level `import requests` tolerant of the
   package being absent. That import only serves the online Face++ API; this tool is
   fully offline, so excluding `requests` (together with urllib3/ssl, about 10 MB) is safe.

## 2. Model weights / 模型权重

**Not distributed here.** Download them yourself, see `models/README.md`.
**本仓库不分发。** 请自行获取，见 `models/README.md`。

| Model | License | Commercial use | Used by this project |
|---|---|---|---|
| BiRefNet (birefnet-v1-lite) | MIT | yes | default "high quality" tier |
| MODNet (hivision_modnet) | Apache-2.0 | yes | "fast" tier |
| BRIA RMBG-1.4 / RMBG-2.0 | CC BY-NC 4.0 / BRIA agreement | **no** | **not used** — deliberately avoided |

⚠️ `rembg` switched its default model to `bria-rmbg` (RMBG-2.0) in v2.0.80. If you build
anything commercial on top of `rembg`, you are using a non-commercial weight by default.
This project deliberately avoids it.

⚠️ `rembg` 从 v2.0.80 起把默认模型换成了 `bria-rmbg`（RMBG-2.0）。基于 `rembg` 做商用
产品的话，默认就在使用非商用权重。本项目刻意避开了它。

## 3. Runtime libraries / 运行时库

| Library | License |
|---|---|
| [OpenCV](https://github.com/opencv/opencv-python) | Apache-2.0 |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | MIT |
| [NumPy](https://github.com/numpy/numpy) | BSD-3-Clause |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU (HPND) |
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) | MIT |
| [tkinterdnd2](https://github.com/pmgagne/tkinterdnd2) | MIT (bundles tkdnd, Tcl license) |
| [mtcnn-runtime](https://pypi.org/project/mtcnn-runtime/) | see its package metadata |
| Python | PSF-2.0 |

Exact terms live in each project's own LICENSE file; that is authoritative.

## 4. Non-code assets / 非代码资源

* `docs/screenshot.png` — a screenshot of this program's own UI, taken with the preview
  panes empty. No real person appears in it.

---

If you are the author of one of these projects and would like the attribution
changed, please open an issue. / 如有疏漏或希望调整署名方式，欢迎开 issue。
