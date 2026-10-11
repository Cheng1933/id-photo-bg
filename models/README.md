# Models / 模型放这里

This folder is intentionally empty in the repository — model weights are big
(hundreds of MB) and each has its own license. Download them yourself and drop
them here.

本目录在仓库里是空的：权重体积大、且各有各的许可证，请自己下载后放进来。

## Expected files / 需要的文件

| File name (exact) | Role | Size | License |
|---|---|---|---|
| `birefnet-v1-lite.onnx` | "High quality" tier (default) | ~89 MB (int8) / ~214 MB (fp32) | MIT |
| `hivision_modnet.onnx` | "Fast" tier | ~25 MB | Apache-2.0 |

The program lists whatever `.onnx` files it finds here. You can also add your own
through the **＋ 添加模型** button in the UI instead of copying files by hand.

程序会列出这里找到的模型；也可以直接用界面上的「＋ 添加模型」按钮添加。

## Where to download / 去哪里下

**hivision_modnet.onnx** — from the HivisionIDPhotos release:

    https://github.com/Zeyi-Lin/HivisionIDPhotos/releases/download/pretrained-model/hivision_modnet.onnx

**birefnet-v1-lite** — two options:

* The official BiRefNet release (fp32, ~214 MB):
  https://github.com/ZhengPeng7/BiRefNet/releases/tag/v1
* Or quantize it to int8 yourself (~89 MB, visually identical output, which is
  what this project ships). Recipe: `docs/quantize.md`.

If GitHub is slow where you are, prepend a mirror, e.g.
`https://ghfast.top/https://github.com/...` (this project downloads over the
network only at setup time; the built tool itself is fully offline).

## Adding your own model / 添加自己的模型

Click **＋ 添加模型** in the UI, pick a `.onnx` file, give it a name and choose its
type. The type matters: different architectures expect different input sizes and
normalisation, and picking the wrong one produces completely wrong matting —
not "slightly worse", but wrong. The tool tries to guess the type from the
model's input size.

点界面上的「＋ 添加模型」，选一个 `.onnx`，起名字并选类型。**类型很重要**：不同架构
的输入尺寸和归一化方式不同，选错会抠出完全错误的结果（不是「差一点」，是「根本不对」）。
程序会尽量按输入尺寸自动猜一个。

## License warning / 许可提醒

Only put weights here that you are allowed to use. In particular **BRIA RMBG-1.4
and RMBG-2.0 are non-commercial only** — this project does not use them, and a
commercial product built on this project should not either.

只放你有权使用的权重。特别注意 **BRIA RMBG-1.4 / RMBG-2.0 仅限非商用**。
