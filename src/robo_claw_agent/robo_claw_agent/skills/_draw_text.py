"""
이미지에 한글 라벨을 그리기 위한 공용 유틸.

cv2.putText는 한글(비ASCII)을 렌더링하지 못해 `?`/공백으로 깨진다.
PIL(Pillow)로 한글 폰트를 사용해 텍스트를 그리고, PIL/폰트를 쓸 수 없으면
cv2.putText로 폴백한다(영문은 정상, 한글은 깨질 수 있음).
"""

import logging
import os

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# 한글 지원 폰트 후보 (우선순위 순). 런타임 환경에 따라 존재하는 첫 번째를 사용.
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumSquareR.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]

_font_path: str | None = None
_font_resolved = False


def _resolve_font_path() -> str | None:
    """사용 가능한 한글 폰트 경로를 1회 탐색해 캐시한다."""
    global _font_path, _font_resolved
    if _font_resolved:
        return _font_path

    _font_resolved = True
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            _font_path = path
            break
    else:
        # fc-match로 시스템 한글 폰트 자동 탐색
        try:
            import subprocess

            out = subprocess.run(
                ["fc-match", "-f", "%{file}", ":lang=ko"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            cand = out.stdout.strip()
            if cand and os.path.exists(cand):
                _font_path = cand
        except Exception as e:
            logger.debug("fc-match font search failed: %s", e)

    if _font_path:
        logger.info("Korean label font: %s", _font_path)
    else:
        logger.warning("Korean font not found, falling back to cv2 (Korean text may render incorrectly)")
    return _font_path


def draw_label(
    img_bgr: np.ndarray,
    text: str,
    x: int,
    y: int,
    font_size: int = 20,
    text_color: tuple[int, int, int] = (255, 255, 255),
    bg_color: tuple[int, int, int] | None = (0, 0, 0),
) -> np.ndarray:
    """이미지(BGR)의 (x, y)를 좌상단으로 하여 텍스트 라벨을 그린다.

    색상은 모두 BGR 순서. bg_color가 None이면 배경 박스를 그리지 않는다.
    PIL 사용 가능 시 한글을 정상 렌더링하고, 실패 시 cv2.putText로 폴백한다.
    반환값은 그려진 동일 이미지(in-place 수정).
    """
    font_path = _resolve_font_path()
    if font_path is not None:
        try:
            from PIL import Image, ImageDraw, ImageFont

            font = ImageFont.truetype(font_path, font_size)
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)
            draw = ImageDraw.Draw(pil_img)

            # 텍스트 경계 박스 계산
            try:
                left, top, right, bottom = draw.textbbox((x, y), text, font=font)
            except AttributeError:  # 구버전 Pillow
                tw, th = draw.textsize(text, font=font)
                left, top, right, bottom = x, y, x + tw, y + th

            if bg_color is not None:
                pil_bg = (bg_color[2], bg_color[1], bg_color[0])  # BGR→RGB
                draw.rectangle(
                    [left - 3, top - 2, right + 3, bottom + 2], fill=pil_bg
                )

            pil_text = (text_color[2], text_color[1], text_color[0])  # BGR→RGB
            draw.text((x, y), text, font=font, fill=pil_text)

            img_bgr[:] = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
            return img_bgr
        except Exception as e:
            logger.debug("PIL Korean rendering failed, falling back to cv2: %s", e)

    # 폴백: cv2.putText (한글은 깨질 수 있음). baseline 보정 위해 y에 글자 높이 가산.
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = font_size / 30.0
    thickness = max(1, int(scale * 2))
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    if bg_color is not None:
        cv2.rectangle(
            img_bgr, (x - 3, y - 2), (x + tw + 3, y + th + 4), bg_color, -1
        )
    cv2.putText(
        img_bgr, text, (x, y + th), font, scale, text_color, thickness, cv2.LINE_AA
    )
    return img_bgr
