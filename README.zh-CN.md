<p align="center">
  <img src="docs/screenshots/forza-painter-fh6-showcase.png" alt="Forza-Painter FH6 展示图">
</p>

# Forza-Painter FH6

**Forza Horizon 6 乙烯基导入工具。** 将图片转换成 Forza 兼容的乙烯基几何数据，预览结果，并从一个桌面应用导入到 FH6 Vinyl Group Editor。

<p>
  <a href="README.md">English</a> |
  <a href="README.es-ES.md">Español</a> |
  <a href="README.es-MX.md">Español MX</a> |
  <a href="README.zh-CN.md">中文</a> |
  <a href="README.ko-KR.md">한국어</a>
</p>

<p>
  <code>v1.8.4</code> <code>Windows</code> <code>Forza Horizon 6</code> <code>GPU/OpenCL</code> <code>单文件 EXE</code>
</p>

## 功能概览

Forza-Painter FH6 围绕当前 FH6 乙烯基制作流程构建：

- 从 PNG、JPG 或 BMP 图片生成 geometry JSON。
- 在导入前预览生成的 JSON。
- 将 geometry JSON 导入未分组的 FH6 乙烯基模板。
- 在应用内 Market 浏览并下载社区预设。
- 使用区域绘制强化重要区域的细节。
- 为研究流程导出和导入实验性的完整形状/type-code JSON。
- 将日志、运行数据、预览和导入诊断保存在本地应用文件夹中。

普通用户应从 [Releases](https://github.com/Daiivr/Forza-Painter-FH6/releases) 下载 EXE。除非你要开发项目，否则不需要 Python、虚拟环境或源码 ZIP。

## 当前应用

以下截图来自当前 `v1.8.4` 桌面界面。

<table>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/app-generate-json-current.png" alt="生成 JSON 页面">
      <strong>生成 JSON</strong><br>
      添加源图片，选择质量预设，调整生成设置，并查看进度。
    </td>
    <td width="50%">
      <img src="docs/screenshots/app-import-current.png" alt="导入页面">
      <strong>导入</strong><br>
      选择 FH6 进程，输入准确的模板层数，预览 JSON 并导入。
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/app-region-paint-current.png" alt="区域绘制页面">
      <strong>区域绘制</strong><br>
      生成基础结果，选择关键区域，把额外图层用在最需要细节的地方。
    </td>
    <td width="50%">
      <img src="docs/screenshots/app-full-shapes-current.png" alt="导出页面">
      <strong>导出</strong><br>
      用于完整形状 JSON 研究的实验性 FH6 shape word 导出/导入工具。
    </td>
  </tr>
</table>

## 游戏内流程

<table>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/fh6-template-ready.png" alt="FH6 模板已准备好">
      <strong>准备模板</strong><br>
      打开 FH6 Vinyl Group Editor，加载球形图层模板，并取消分组。
    </td>
    <td width="50%">
      <img src="docs/screenshots/fh6-import-result.png" alt="FH6 导入结果">
      <strong>导入 JSON</strong><br>
      保持编辑器打开，让应用定位可编辑图层表并写入设计。
    </td>
  </tr>
  <tr>
    <td width="50%">
      <img src="docs/screenshots/app-import-preview.png" alt="应用 JSON 预览">
      <strong>检查预览</strong><br>
      JSON 预览适合检查图层，但游戏内结果才是最终参考。
    </td>
    <td width="50%">
      <img src="docs/screenshots/fh6-car-applied.png" alt="FH6 车身应用结果">
      <strong>应用到车辆</strong><br>
      导入并保存后，像使用其他 FH6 设计一样使用该乙烯基组。
    </td>
  </tr>
</table>

## 快速开始

1. 从 [Releases](https://github.com/Daiivr/Forza-Painter-FH6/releases) 下载 `forza-painter-fh6-v1.8.4.exe`。
2. 将 EXE 放到普通可写文件夹中，例如 `Desktop\forza-painter-fh6`。
3. 运行 EXE。如果 Windows 进程权限导致导入失败，请以管理员身份运行。
4. 在 FH6 中打开 `Create Vinyl Group` / `Vinyl Group Editor`。
5. 加载球形模板，取消分组，并记下游戏显示的准确图层数。
6. 在 Forza-Painter FH6 中生成或添加 JSON，选择 FH6 进程，输入模板层数，然后导入。

## 生成 JSON

生成 JSON 页面使用内置 GPU/OpenCL 生成器，把图片转换成 Forza 友好的几何文件。

1. 添加一张或多张图片。
2. 选择质量预设。
3. 可选：打开 `质量设置`，调整图层数、分辨率、随机样本等高级参数。
4. 开始生成，等待预览和日志更新。
5. 使用能适配模板的最高图层 JSON。

生成文件会保存在源图片旁边。一张图片可能会生成 `image.500.json`、`image.1000.json`、`image.3000.json` 等 checkpoint，以及最终的 `image.json`。

## 导入 JSON

导入页面会把生成的几何数据写入当前 FH6 Vinyl Group Editor 会话。

- FH6 模板在导入前必须取消分组。
- 应用中输入的图层数必须与游戏完全一致。
- 导入时保持 FH6 停留在 Vinyl Group Editor。
- 应用扫描或写入时不要切换菜单。
- 如果 Windows 阻止进程访问，请以管理员身份重启应用。

FH6 需要额外的边界图层才能正确保存和应用边界。例如，1000 层 JSON 应使用至少 1004 层模板；3000 层模板通常约有 2996 层可绘制图层。

## 区域绘制

区域绘制适用于部分区域需要更多细节的图片。它先生成基础结果，让你选择矩形或椭圆区域，然后只在选中区域投入额外图层。

当前工具包括：

- 第一轮和区域轮次的图层预算。
- 矩形和椭圆选择。
- 拖动、缩放、旋转和滚轮控制。
- 预览与热力图标签页。
- 轮次历史、剩余图层跟踪和结果 JSON 导出。

## Market

导入页面包含用于 painter6.com 预设的应用内 Market 按钮。你可以浏览设计、预览选中的预设、下载 geometry JSON，并把下载的 JSON 直接加入导入列表。

## 导出

导出功能仍然是实验性的。它面向导出的或手写的 FH6 type-code JSON，不是普通生成器输出的椭圆几何。

- 使用层偏移 `0x7A` 处的 16 位 FH6 shape word。
- 导出稳定的视觉字段，例如位置、缩放、旋转、倾斜、颜色、蒙版/banner 数据和 shape word。
- 避免复制 `0xA8` 等易变资源指针。
- 可用时使用内置 FH6 乙烯基资源进行预览。

普通生成的 geometry JSON 请使用标准导入页面。

## 运行文件夹

单文件 EXE 会临时解压内部文件，并把正常应用数据写在 EXE 旁边：

- `runtime/`：日志、生成预览、区域绘制会话、Market 下载和临时文件。
- `webui-data/`：本地偏好设置和 FH6 探测/会话缓存。

关闭应用后可以删除这些文件夹来重置本地运行数据。

## 故障排查

- **导入没有开始：** 以管理员身份运行应用，并确认 FH6 编辑器已打开。
- **找不到模板：** 取消分组，输入准确图层数，并在扫描时留在编辑器中。
- **结果发糊：** 提高输出图层数和 `Random samples`；超过 `200000` 通常会改善最终清晰度。
- **预览与 FH6 不同：** 当前 JSON 预览是近似值，因为 FH6 保留了应用预览会简化的小数椭圆尺寸。
- **GPU/OpenCL 错误：** 更新 NVIDIA、AMD 或 Intel 显卡驱动。
- **需要调试帮助：** 使用 `导出详细日志`，并在提交 issue 时附上日志。

## 开发

从源码运行主要用于开发和测试：

```powershell
install_dependencies.bat
start_app.bat
```

常用项目文件：

- `src/app.py`：桌面 UI 和工作流程。
- `src/generator_backend.py`：生成器命令与构建集成。
- `src/import_readiness.py`：导入前检查。
- `src/region_painter/`：区域绘制工作流模块。
- `scripts/make_exe_release.ps1`：发布打包脚本。
- `CHANGELOG.md`：完整版本历史。

## 资源

- Releases: [github.com/Daiivr/Forza-Painter-FH6/releases](https://github.com/Daiivr/Forza-Painter-FH6/releases)
- 导入演示视频: [bilibili.com/video/BV1hG5Z6nENZ](https://www.bilibili.com/video/BV1hG5Z6nENZ)
- 内置 GPU 生成器参考: [zjl88858/forza-painter-geometrize-gpu](https://github.com/zjl88858/forza-painter-geometrize-gpu)
- 完整 changelog: [CHANGELOG.md](CHANGELOG.md)

## 许可证

请参阅 [LICENSE](LICENSE)、[LICENSE.custom-importer](LICENSE.custom-importer) 和 [LICENSE.kloudys-custom-importer](LICENSE.kloudys-custom-importer)。
