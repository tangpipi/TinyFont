# -*- coding: utf-8 -*-
import struct
import sys
import os
import collections

try:
    from colorama import init, Fore, Style
    init()
except Exception:
    class _Dummy:
        def __getattr__(self, name):
            return ''

    Fore = Style = _Dummy()

def analyze_tyf(filename):
    """Analyze a VECF font file and print a comprehensive audit report.

    Args:
        filename (str): Path to the VECF file to analyze.

    Returns:
        None: Results are printed to stdout.
    """

    if not os.path.exists(filename):
        print(f"Error: File {filename} not found.")
        return

    file_size = os.path.getsize(filename)
    with open(filename, 'rb') as f:
        data = f.read()

    if data[0:4] != b'VECF':
        print("Error: Not a VECF file.")
        return

    # 1. 解析基础头部
    font_id, g_flags, cnt_section = struct.unpack('<IHH', data[4:12])

    # 2. 初始化空间统计计数器
    stats = {
        'header': 12,
        'index': cnt_section * 8,
        'block_header': cnt_section * 4,
        'length_table': 0,
        'pure_data': 0,
        'meta_data': 0,
        'padding': 0,
        'empty_slot_loss': 0,
    }

    # 初始化按 plane 的统计和元数据模式计数
    plane_map = collections.defaultdict(lambda: {'chars': 0, 'sections': 0, 'slots': 0})
    mode_counts = {'imm_y': 0, 'complex_y': 0, 'imm_w': 0, 'complex_w': 0}

    # 收集所有字符及其字节长度，供后续模拟打包使用
    # 存储元组 (unicode_codepoint, glyph_data_bytes)
    all_chars_data = []

    glyph_details = []

    # 3. 扫描字形节并统计各项数据
    idx_ptr = 12
    last_block_end = 12 + stats['index']

    for i in range(cnt_section):
        props, start_code, count_val, p0, p1, p2 = struct.unpack('<HHBBBB', data[idx_ptr:idx_ptr+8])
        idx_ptr += 8

        plane_id = (props >> 11) & 0x1F
        has_complex_y = bool(props & 0x01)
        has_complex_w = bool((props >> 1) & 0x01)

        start_uni = (plane_id << 16) | start_code
        glyph_count = count_val + 1
        abs_offset = (p0 | (p1 << 8) | (p2 << 16)) << 2

        # 计算本块之前的对齐填充字节数
        if abs_offset > last_block_end:
            stats['padding'] += (abs_offset - last_block_end)

        # 解析块头部（4 字节）
        imm_y, imm_w, meta_off = struct.unpack('<bbH', data[abs_offset:abs_offset+4])

        # 更新元数据模式计数
        if has_complex_y:
            mode_counts['complex_y'] += 1
        else:
            mode_counts['imm_y'] += 1
        if has_complex_w:
            mode_counts['complex_w'] += 1
        else:
            mode_counts['imm_w'] += 1

        plane_map[plane_id]['sections'] += 1
        plane_map[plane_id]['slots'] += glyph_count
        stats['length_table'] += glyph_count  # 长度表大小 = glyph_count 字节

        # --- 修正点开始：按照新规范解析 Block ---
        # 结构：BlockHeader(4) -> LengthTable(count) -> GlyphDataStream(...)
        len_table_start = abs_offset + 4
        stream_start = len_table_start + glyph_count
        
        current_data_offset = 0 # 记录在 Stream 中的相对偏移

        for j in range(glyph_count):
            # 1. 从长度表读取长度 (1 Byte)
            g_len = data[len_table_start + j]
            
            # 2. 转换为数据字节数 (PointCount * 2)
            data_bytes = g_len << 1
            
            if g_len == 0:
                stats['empty_slot_loss'] += 1 # 长度表里占了1字节但无数据
            else:
                plane_map[plane_id]['chars'] += 1
                stats['pure_data'] += data_bytes
                uni = start_uni + j

                # 将 (unicode, 数据字节数) 添加至列表
                all_chars_data.append((uni, data_bytes))

                # 3. 从数据流中提取坐标数据
                # 绝对位置 = Stream起始 + 当前累加偏移
                ptr_data = stream_start + current_data_offset
                pts = data[ptr_data : ptr_data + data_bytes]
                
                # 统计笔画数 (Bit 7 set)
                stk = sum(1 for k in range(0, len(pts), 2) if pts[k] & 0x80)
                glyph_details.append({'bytes': data_bytes, 'pts': g_len, 'stk': stk})
                
                # 累加数据流偏移
                current_data_offset += data_bytes

        # 更新本块结束位置指针 (Header + LenTable + DataStream)
        curr_block_end = stream_start + current_data_offset
        last_block_end = curr_block_end
        # --- 修正点结束 ---

    # 统计文件末尾的多余填充（如果有）
    if file_size > last_block_end:
        stats['padding'] += (file_size - last_block_end)

    # 4. 全局阈值扫描优化器（模拟不同 gap 阈值下的结构开销）
    all_chars_data.sort(key=lambda x: x[0])
    
    def simulate_packing(threshold):
        """Simulate packing cost for a given gap threshold.

        Args:
            threshold (int): Maximum gap (in codepoints) allowed within a section.

        Returns:
            tuple: (total_struct_bytes, number_of_sections)
        """
        if not all_chars_data:
            return 0, 0

        sim_sections = 1
        sim_empty_slots = 0
        sim_padding = 0

        # 当前段已占用的槽位（包含空洞）
        curr_sec_slots = 1
        # 当前块的长度
        # Header(4) + LenTable(1) + Data(sz)
        curr_block_len = 4 + 1 + all_chars_data[0][1]

        for k in range(1, len(all_chars_data)):
            curr_u, curr_sz = all_chars_data[k]
            prev_u, _ = all_chars_data[k-1]
            gap = curr_u - prev_u - 1

            # 决定是否拆分为新段：过大 gap、当前段槽位超限或跨 plane
            is_too_wide = (curr_sec_slots + gap + 1) > 256
            is_new_plane = (prev_u >> 16) != (curr_u >> 16)

            if gap > threshold or is_too_wide or is_new_plane:
                # 结算上一段的对齐填充
                if curr_block_len % 4 != 0:
                    sim_padding += (4 - (curr_block_len % 4))

                # 开启新段并重置计数
                sim_sections += 1
                curr_sec_slots = 1
                curr_block_len = 4 + 1 + curr_sz
            else:
                # 继续当前段
                # 增加 gap 个长度表空项(1B each) + 当前项长度表(1B) + 数据大小
                sim_empty_slots += gap
                curr_sec_slots += (gap + 1)
                curr_block_len += gap + 1 + curr_sz

        # 结算最后一段的对齐填充
        if curr_block_len % 4 != 0:
            sim_padding += (4 - (curr_block_len % 4))

        # 总结构成本 = 全局 header(12B) + Index(8B*N) + BlockHeader(4B*N) + LengthTable(N + Gaps) + Padding
        # 注：代码中 stats['index'] 单独计算了，这里 total_struct 主要评估随分段变化的开销
        # Cost = (Header12 + Index8*N) + (BlockHeader4*N + LenTableBytes + Padding)
        # LenTableBytes = count_of_chars + count_of_gaps
        total_struct = (sim_sections * 12) + (len(all_chars_data) + sim_empty_slots) + sim_padding
        return total_struct, sim_sections

    # 执行阈值扫描（1..64）
    scan_results = []
    print(f"\n{Fore.CYAN}[Running Optimization Scan 1..64]{Style.RESET_ALL}...")
    for t in range(1, 65):
        cost, secs = simulate_packing(t)
        scan_results.append({'threshold': t, 'cost': cost, 'sections': secs})
    
    # 选择最小成本对应的阈值
    best_opt = min(scan_results, key=lambda x: x['cost'])
    
    # 输出报告
    print(f"\n{Fore.MAGENTA}VECF Comprehensive Audit Report{Style.RESET_ALL} | FontID: 0x{font_id:08X}")
    print(f"{Fore.YELLOW}File Path:{Style.RESET_ALL} {filename}")
    print("-" * 75)

    print(Fore.CYAN + f"{'Component':<20} {'Bytes':<10} {'Percentage':<12} {'Notes'}" + Style.RESET_ALL)
    print(f"{'Global Header':<20} {stats['header']:<10} {stats['header']/file_size*100:>8.1f}%     Fixed 12B")
    print(f"{'Index Table':<20} {stats['index']:<10} {stats['index']/file_size*100:>8.1f}%     8B per section")
    print(f"{'Block Headers':<20} {stats['block_header']:<10} {stats['block_header']/file_size*100:>8.1f}%     4B per block")
    print(f"{'Length Table':<20} {stats['length_table']:<10} {stats['length_table']/file_size*100:>8.1f}%     Incl. {stats['empty_slot_loss']}B gaps")
    print(f"{'Pure Glyph Data':<20} {stats['pure_data']:<10} {stats['pure_data']/file_size*100:>8.1f}%     XY Coordinates")
    print(f"{'Alignment Padding':<20} {stats['padding']:<10} {stats['padding']/file_size*100:>8.1f}%     For 4B alignment")
    print("-" * 75)
    print(f"{'TOTAL':<20} {file_size:<10} {100.0:>8.1f}%")
    print("-" * 75)

    print(f"{Fore.CYAN}[Plane Distribution]{Style.RESET_ALL}")
    for pid in sorted(plane_map.keys()):
        p = plane_map[pid]
        print(f" Plane {pid:<2}: {p['chars']:>5} chars in {p['sections']:>3} sections (Fill: {p['chars']/p['slots']*100:>4.1f}%)")

    print(f"\n{Fore.CYAN}[Metadata Mode]{Style.RESET_ALL}")
    print(f" Y-Offset: {mode_counts['imm_y']:>3} Sections use Immediate, {mode_counts['complex_y']:>3} use Table")
    print(f" Width:    {mode_counts['imm_w']:>3} Sections use Immediate, {mode_counts['complex_w']:>3} use Table")

    print(f"\n{Fore.CYAN}[Glyph Size Histogram]{Style.RESET_ALL}")
    buckets = collections.defaultdict(int)
    for g in glyph_details: buckets[(g['bytes'] // 20) * 20] += 1
    for b_range in sorted(buckets.keys()):
        count = buckets[b_range]
        bar = "#" * int((count / len(glyph_details)) * 40) if glyph_details else ""
        print(f" {b_range:3d}-{b_range+19:3d} B: {count:5d} | {bar}")

    # 优化分析与建议（基于扫描结果）
    actual_struct = stats['index'] + stats['block_header'] + stats['length_table'] + stats['padding']
    ideal_struct = best_opt['cost']
    waste = actual_struct - ideal_struct
    
    print(f"\n{Fore.CYAN}[Optimization Analysis]{Style.RESET_ALL}")
    # 粗略估算当前 gap 使用情况
    print(f" Current Structure Cost: {actual_struct} Bytes (Gap used ~{int(stats['empty_slot_loss'] / (cnt_section or 1) * 2)})")
    print(f" Optimal Structure Cost: {ideal_struct} Bytes (at Gap Threshold = {best_opt['threshold']})")
    
    # 绘制简单 ASCII 曲线（阈值 vs 成本）
    print(f"\n {Fore.CYAN}[Scan Result Visualization]{Style.RESET_ALL}")
    min_c = min(r['cost'] for r in scan_results)
    max_c = max(r['cost'] for r in scan_results)
    range_c = max_c - min_c + 1
    
    # 选择用于展示的阈值刻度（包含最佳阈值）
    show_ticks = [1, 5, 10, 14, 20, 30, 40, 50, 60, best_opt['threshold']]
    show_ticks = sorted(list(set(show_ticks)))
    
    for t in show_ticks:
        # 获取阈值 t 的扫描结果并归一化以构建条形长度
        res = next(r for r in scan_results if r['threshold'] == t)
        c = res['cost']
        bar_len = int((c - min_c) / range_c * 40)
        marker = f"({c}B) <-BEST" if t == best_opt['threshold'] else f"({c}B)"
        print(f" Gap {t:2d}: {'#' * bar_len}{' ' * (40-bar_len)} {marker}")

    print("-" * 75)
    if waste > 100:  # 容差阈值：100 字节
        print(Fore.RED + f" RECOMMENDATION: Re-pack with 'gap > {best_opt['threshold']}' to save {waste} Bytes." + Style.RESET_ALL)
    else:
        print(Fore.GREEN + f" STATUS: Optimized. Your current gap strategy is mathematically optimal." + Style.RESET_ALL)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"{Fore.YELLOW}Usage:{Style.RESET_ALL} python analyze_vecf.py <font.vecf>")
    else:
        analyze_tyf(sys.argv[1])