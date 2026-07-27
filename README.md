# PNG Slice 清洗工具

人工审阅并清理 CT 影像 PNG slice 文件夹的 Tkinter GUI 工具。

## 功能

- **左右分栏浏览**：左栏显示正向/逆向 slice 预览、亮度对比图、单帧详情；右栏为病人质量记录、Slice 操作、清洗记录表
- **Slice 预览**：每层 slice 的 PNG 动图播放，支持拖拽多选、右键取消、`Ctrl+单击`切换和键盘顺序选择
- **首帧亮度对比**：默认显示缩略图；勾选“原图大小”后按原始尺寸显示，并可在概览区勾选/框选删除 slice
- **删除/撤销**：选中 slice 移动到回收目录 `.slice_cleaner_trash`，支持撤销
- **质量记录**：勾选问题标签（亮度不统一/图像不完整/图像不清晰）+ 自定义问题描述，保存到 CSV
- **清洗记录表**：右侧 Treeview 显示所有病人标记状态，单击导航、双击打开文件夹；点击“问题”按有无问题排序，点击“编号 / 病人 ID”恢复自然编号排序
- **加载模式**：
  - **数据清洗**：完整预加载下一位病人的动画帧
  - **快速浏览**：预加载后续 5 位病人的所有首帧，并优先加载每位病人正向前两层和反向前两层的完整动画
- **进度恢复**：重新打开同一数据目录时自动回到上次浏览的病人
- **JSON 查看**：支持打开当前病人文件夹内的第一个 JSON 文件

## 快捷键

| 按键            | 功能                                    |
| --------------- | --------------------------------------- |
| `Space`         | 下一个病人                              |
| `Tab`           | 上一个病人                              |
| `←` / `→`       | 上一个 / 下一个病人                     |
| `1` / `2`       | 正向 / 反向依次勾选下一个 slice         |
| `3` / `4`       | 从最后一次选择开始，正向 / 反向取消勾选 |
| `Q` / `W` / `E` | 切换三个质量问题标记                    |
| `D` / `Ctrl+D`  | 删除所选 slice                          |
| `Z` / `Ctrl+Z`  | 撤销删除                                |
| `Ctrl+S`        | 保存当前记录                            |
| `Ctrl+A`        | 打开清洗记录 CSV                        |
| `Ctrl+F`        | 打开当前病人 JSON                       |
| `Ctrl+X`        | 清除当前病人记录                        |

## 环境要求

- Python 3.12+
- 无外部依赖（仅使用标准库：tkinter、pathlib、csv 等）

## 快速开始

```bash
# 使用默认数据目录
python png_slice_cleaner.py

# 指定数据目录
python png_slice_cleaner.py D:/path/to/data

# 指定记录文件路径
python png_slice_cleaner.py D:/path/to/data --records D:/records.csv
```

## 数据目录结构

```
data_root/
├── 病人A/
│   ├── slice_001/
│   │   ├── frame_0001.png
│   │   ├── frame_0002.png
│   │   └── ...
│   ├── slice_002/
│   └── ...
├── 病人B/
│   └── ...
├── 清洗记录.csv          # 自动生成
└── .png_slice_cleaner_state  # 自动保存上次浏览位置
```

## 输出文件

- `清洗记录.csv` — 病人质量记录（UTF-8 with BOM 编码）
- `.png_slice_cleaner_state` — 最后浏览的病人位置（自动生成，无需手动创建）
- `.slice_cleaner_trash/` — 删除的 slice 移入此目录，脚本退出时自动清理

## 注意事项

- **Windows 专属**：使用 `os.startfile` 打开文件夹 / CSV
- **CSV 锁定**：在 Excel 中打开 CSV 会锁定文件，此时保存记录会失败并弹出提示
- **状态文件**：删除 `.png_slice_cleaner_state` 后，下次打开会从第一位病人开始
- 脚本退出时自动删除 `.slice_cleaner_trash` 回收目录
