"""Landmark indices of the 478-point MediaPipe face mesh.

"Left"/"right" are the SUBJECT's (driver's) left/right. In a non-mirrored camera image the driver's
right eye is on the image-left side. Inference must run on the non-mirrored frame.
"""

from __future__ import annotations

# EAR points p1..p6 (Soukupova & Cech 2016): p1/p4 corners, p2/p3 upper lid, p5/p6 lower lid.
RIGHT_EYE_EAR: tuple[int, ...] = (33, 160, 158, 133, 153, 144)
LEFT_EYE_EAR: tuple[int, ...] = (362, 385, 387, 263, 373, 380)

# Corners in image-left -> image-right order (used for the iris position along the eye axis)
RIGHT_EYE_CORNERS: tuple[int, int] = (33, 133)  # outer, inner
LEFT_EYE_CORNERS: tuple[int, int] = (362, 263)  # inner, outer

RIGHT_IRIS_CENTER = 468
LEFT_IRIS_CENTER = 473

NOSE_TIP = 1

# Contours for drawing
# fmt: off
RIGHT_EYE_CONTOUR: tuple[int, ...] = (33, 246, 161, 160, 159, 158, 157, 173, 133, 155, 154, 153, 145, 144, 163, 7)
LEFT_EYE_CONTOUR: tuple[int, ...] = (263, 466, 388, 387, 386, 385, 384, 398, 362, 382, 381, 380, 374, 373, 390, 249)
FACE_OVAL: tuple[int, ...] = (
    10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377,
    152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109,
)
# fmt: on
