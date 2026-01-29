# TinyFont Binary Format Specification

**Version:** 0.1.5
**Extension:** `.tyf`
**Magic:** `VECF`

## 1. Overview

TinyFont 是一种专为嵌入式系统设计的轻量级矢量字体格式。其设计目标是最小化 Flash 占用和 RAM 解析开销。

## 2. File Structure

文件由全局文件头、索引表、数据块序列组成。

```text
+-------------------+ <--- Offset 0x00
| Global Header     | Fixed 12 Bytes
+-------------------+
| Section Index     | 8 Bytes * Section Count
| [Entry 0]         |
| [Entry 1]         |
| ...               |
+--[   Padding   ]--+ 
| Section Block 0   | <--- 4-Byte Aligned
|   - Block Header  |       Variable Size
|   - Length Table  |
|   - Glyph Data    |
|  [   Padding   ]  |
|   - Meta Data     | <--- 4-Byte Aligned
+-------------------+
| Section Block 1   |
| ...               |
+-------------------+

```

## 3. Data Structures

所有多字节整数通常遵循 **Little-Endian (小端序)**，除非 `flags` 另有说明。

### 3.1 Global Header

位于文件起始处，固定 12 字节。

```c
typedef struct {
    uint8_t  magic[4];       // [0x00] 固定标识 "VECF"
    uint32_t font_id;        // [0x04] 版本号或唯一字体ID
    uint16_t flags;          // [0x08] 全局属性位 (Res: Reserved)
    uint16_t section_count;  // [0x0A] 索引表项数量 N
} GlobalHeader;

```

### 3.2 Section Index Entry

紧跟 Header 之后，由 `section_count` 决定项数。每个索引项描述一组连续的 Unicode 字符。

```c
typedef struct {
    // [0x00 - 0x01] 属性与平面 ID
    // Bit 15-11 (5 bits): Unicode Plane ID (0-31), 覆盖 U+00000 - U+1FFFFF
    // Bit 10-02 (9 bits): Reserved
    // Bit 1     (1 bit) : Has_Complex_Y (1 = Y偏移不固定，需查表; 0 = 使用立即数)
    // Bit 0     (1 bit) : Has_Complex_W (1 = 宽度不固定，需查表; 0 = 使用立即数)
    uint16_t props;

    // [0x02 - 0x03] 起始字符编码 (低 16 位)
    // 实际 Unicode Start = (Plane_ID << 16) | start_code
    uint16_t start_code;

    // [0x04] 字符容量
    // 本 Section 包含的字符数量 = count + 1 (范围 1-256)
    uint8_t  count;

    // [0x05 - 0x07] Flash 物理指针 (24 bits)
    // 指向 Section Block 的绝对偏移量 (相对于文件头)
    // 实际地址 = (ptr[0] | ptr[1]<<8 | ptr[2]<<16) << 2
    // 注意：Section Block 起始地址必须 4 字节对齐
    uint8_t  ptr[3]; 
} SectionIndexEntry;

```

### 3.3 Section Block

每个 Section 指向的数据块。包含块头、字形流和可选的元数据。

#### 3.3.1 Block Header

```c
typedef struct {
    // [0x00] 全局宽度 (立即数)
    // 当 Index.Has_Complex_W == 0 时，该段所有字符应用此宽度 (Monospace)
    uint8_t  imm_w;

    // [0x01] 全局 Y 偏移 (立即数)
    // 当 Index.Has_Complex_Y == 0 时，该段所有字符应用此偏移
    int8_t   imm_y;


    // [0x02 - 0x03] 元数据相对偏移
    // 指向 Meta Data Area 相对于本 Block Header 起始位置的偏移
    // 实际字节偏移 = meta_offset * 4
    // Block 最大支持大小 = 65535 * 4 = ~256 KB
    uint16_t meta_offset;
} BlockHeader;

```

#### 3.3.2 Glyph Length Table

紧跟在 BlockHeader 之后，这是一个固定长度的数组（由 SectionIndex 中的 `count` 决定）。

将长度表独立存放允许解析器快速遍历或定位特定字形的偏移，而无需逐个解析字形数据。

```c
// Array Size: count + 1 Bytes
// Type: uint8_t array
uint8_t glyph_lengths[count + 1];

```

* **存储值** = `Real_Byte_Size >> 1`
* **实际字节长度** = `Stored_Value * 2`
* **物理含义**：因为字形数据由 (X, Y) 坐标对组成，数据流长度必然是偶数。右移一位存储实际上记录的是 **"坐标点对 (Point Pairs)"** 的数量。

#### 3.3.3 Glyph Data Stream

紧跟在 `Glyph Length Table` 之后。包含纯粹的笔画坐标数据，**不再包含**自身的长度头。

解析第  个字形时，需要根据前  个长度表的累加值来确定起始偏移。

**单个字形结构 (Glyph)：**

```c
typedef struct {
    struct StrokeData {
        // 坐标点数组 (紧密排列)
        uint8_t x, y;
        // 格式：X0, Y0, X1, Y1, ... Xn, Yn
    } Strokes[]; 
} Glyph;

```

**StrokeData 定义：**

* **Byte 0 (X)**: `Bit 7` (Pen Status), `Bit 6-0` (X Coord 0-127)
* **Byte 1 (Y)**: `Bit 7` (Reserved), `Bit 6-0` (Y Coord 0-127)

#### 3.3.4 Meta Data Area

位于 Block 的末尾，由 `BlockHeader.meta_offset` 确定位置。
仅当索引标志位指示需要复杂布局时存在，多个元数据块按照flag顺序逆序排列（从低有效位开始）。

由于meta_offset是以4字节为单位的偏移，因此Meta Data区起始地址总是4字节对齐的。

**数据布局顺序：**

1. **Width Lookup Table** (如果 `Has_Complex_W == 1`)
* 大小：`count + 1` 字节
* 类型：`uint8_t` 数组

2. **Y Lookup Table** (如果 `Has_Complex_Y == 1`)
* 大小：`count + 1` 字节
* 类型：`int8_t` 数组
