# -*- coding: utf-8 -*-
import json
import time
import sys
import argparse
import struct
import os
import random
from PIL import ImageDraw 

try:
    import core_algo
except ImportError:
    print("Error: core_algo.py not found.")
    sys.exit(1)

# -----------------------------------------------------------------------------
# Parameter classes
# -----------------------------------------------------------------------------

class CommonParams:
    """Common configuration parameters used by build and test routines.

    Attributes:
        width: int, image width in pixels.
        height: int, image height in pixels.
        strw: int, nominal stroke width used by the extraction algorithm.
        simplify_eps: float, RDP simplification epsilon.
        spur_len: float, length threshold for spur pruning.
        join_dist: float, maximum distance to join nearby endpoints.
    """

    width = 127
    height = 127
    strw = 10
    simplify_eps = 6.0
    spur_len = 2.0
    join_dist = 6.0

class BuildParams(CommonParams):
    """Parameters specific to the build command.

    Attributes:
        output: str, base name for output files (JSON/TYF).
        full_scan: bool, whether to scan full Unicode range.
    """

    output = "font"
    full_scan = False

class TestParams(CommonParams):
    """Parameters specific to the test command.

    Attributes:
        nsample: int, number of samples to render horizontally.
        corpus: str, optional test string or path to corpus.
    """

    nsample = 8
    corpus = ""

# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------

def get_gb2312_list():
    valid_chars = []
    for row in range(1, 95): 
        for col in range(1, 95):
            b1, b2 = 0xA0 + row, 0xA0 + col
            try:
                char = bytes([b1, b2]).decode('gb2312')
                valid_chars.append(ord(char))
            except: pass
    ascii_chars = list(range(0x20, 0x7F))
    return sorted(list(set(ascii_chars + valid_chars)))

def print_progress(current, total, prefix='Progress', suffix='', length=40):
    percent = ("{0:.1f}").format(100 * (current / float(total)))
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '-' * (length - filled_length)
    sys.stdout.write(f'\r{prefix} |{bar}| {percent}% {suffix}')
    sys.stdout.flush()

def visualize(mtx, ssegs):
    im = core_algo.mtx2im(mtx, n=40).convert("RGB")
    dr = ImageDraw.Draw(im)

    for s in ssegs:
        line_color = (
            random.randint(64, 255), 
            random.randint(64, 255), 
            random.randint(64, 255)
        )
        dr.line(s, fill=line_color, width=1)
        r = 3
        for idx, (x, y) in enumerate(s):
            if idx == 0: color = (255, 255, 0); r=5
            elif idx == len(s) - 1: color = (255, 0, 0); r=3
            else: color = line_color; r=2
            dr.ellipse((x - r, y - r, x + r, y + r), outline=color)
    return im

# -----------------------------------------------------------------------------
# tinyFont binary packer
# -----------------------------------------------------------------------------

from tinyfont import TyfPacker

# -----------------------------------------------------------------------------
# Business logic
# -----------------------------------------------------------------------------

def build(font_path):
    w, h = BuildParams.width, BuildParams.height
    
    if BuildParams.full_scan:
        print("Mode: Full Unicode")
        chars = list(range(0x20, 0x7F)) + list(range(0x4e00, 0x9fef + 1))
    else:
        print("Mode: GB2312 Whitelist")
        chars = get_gb2312_list()
    
    print(f"Total Chars: {len(chars)}")
    print(f"Config: Res={w}x{h}, EPS={BuildParams.simplify_eps}")
    
    packer = TyfPacker()
    json_data = {}
    start_time = time.time()
    def perc(x): return float("%.3f" % x)

    for idx, i in enumerate(chars):
        ch = chr(i)
        mtx = core_algo.rastBox(ch, w=w, h=h, f=font_path)
        
        # Pass all algorithm parameters explicitly to the core extractor.
        ssegs = core_algo.raster_to_strokes(
            mtx, 
            strw=BuildParams.strw,
            simplify_eps=BuildParams.simplify_eps,
            spur_len=BuildParams.spur_len,
            join_dist=BuildParams.join_dist
        )
        
        norm_segs = []
        for seg in ssegs:
            norm_segs.append([(perc(p[0]/float(w)), perc(p[1]/float(h))) for p in seg])
            
        packer.add_glyph(i, norm_segs)
        json_data["U+"+hex(i)[2:].upper()] = norm_segs
        
        if idx % 100 == 0:
            elapsed = time.time() - start_time
            speed = (idx + 1) / (elapsed + 0.001)
            print_progress(idx + 1, len(chars), suffix=f"{speed:.1f} ch/s")

    print("\nWriting files...")
    out_base = BuildParams.output.replace(".json", "").replace(".tyf", "")
    with open(out_base + ".json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, separators=(',', ':'))
    packer.finish(out_base + ".tyf")
    print("All Done.")

def test(fonts):
    w, h = TestParams.width, TestParams.height
    in_order = False
    if len(TestParams.corpus): 
        corpus = TestParams.corpus
        in_order = True
        TestParams.nsample = len(corpus)
    else:
        try: corpus = open("teststrings.txt",'r',encoding='utf-8').readlines()[-1]
        except: corpus = "TheQuickBrownFoxJumpsOverTheLazyDog零一二三四五六七八九"

    IM = core_algo.Image.new("RGB",(w*TestParams.nsample, h*len(fonts)))
    DR = ImageDraw.Draw(IM)
    randidx = random.randrange(0, max(1, len(corpus)//TestParams.nsample))
    
    for i in range(0, TestParams.nsample):
        if in_order:
            idx = i
        else:
            idx = (randidx*TestParams.nsample + i) % len(corpus)
        ch = corpus[idx]
        print(ch, end=" ")
        sys.stdout.flush()
        
        for j in range(0, len(fonts)):
            mtx = core_algo.rastBox(ch, f=fonts[j], w=w, h=h)
            ssegs = core_algo.raster_to_strokes(
                mtx,
                strw=TestParams.strw,
                simplify_eps=TestParams.simplify_eps,
                spur_len=TestParams.spur_len,
                join_dist=TestParams.join_dist
            )
            im = visualize(mtx, ssegs)
            IM.paste(im, (i*w, j*h))

    IM.show()

# -----------------------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Char2Stroke V18 (Split Core)')
    subparsers = parser.add_subparsers(dest='command')

    p_build = subparsers.add_parser('build')
    p_build.add_argument("input", help="Font file path")
    
    p_test = subparsers.add_parser('test')
    p_test.add_argument('fonts', nargs='+', help="Fonts to test")

    def autoparse(parser, params_cls, exclude=None):
        exclude = exclude or []
        attrs = {}
        for cls in reversed(params_cls.__mro__):
            for k in dir(cls):
                if not k.startswith("_"): attrs[k] = getattr(cls, k)
        for k, default_val in attrs.items():
            if k in exclude: continue
            val_type = type(default_val)
            if val_type == bool:
                parser.add_argument(f'--{k}', action='store_true', dest=k)
            else:
                parser.add_argument(f'--{k}', type=val_type, default=default_val, dest=k)

    autoparse(p_build, BuildParams)
    autoparse(p_test, TestParams)

    args = parser.parse_args()

    def load_args(args_obj, params_cls):
        for k in dir(params_cls):
            if not k.startswith("_") and hasattr(args_obj, k):
                setattr(params_cls, k, getattr(args_obj, k))

    if args.command == "build":
        load_args(args, BuildParams)
        build(args.input)
        
        # If an analyze tool exists in the workspace, run it on the generated TYF.
        if os.path.exists("analyze.py"):
            try:
                import analyze
                analyze.analyze_tyf(BuildParams.output)
            except Exception as e:
                print(f"[Analyzer] Failed to run analyze.py: {e}")
        
    elif args.command == "test":
        load_args(args, TestParams)
        test(args.fonts)
    else:
        parser.print_help()