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

RIGHT_EYE_LID_CENTER: tuple[int, int] = (159, 145)  # upper, lower
LEFT_EYE_LID_CENTER: tuple[int, int] = (386, 374)

RIGHT_IRIS_CENTER = 468
LEFT_IRIS_CENTER = 473
RIGHT_IRIS_RING: tuple[int, int, int, int] = (469, 470, 471, 472)
LEFT_IRIS_RING: tuple[int, int, int, int] = (474, 475, 476, 477)

# Inner lip ring for MAR: upper 82/13/312, lower 87/14/317, inner corners 78/308
MAR_VERTICAL_PAIRS: tuple[tuple[int, int], ...] = ((82, 87), (13, 14), (312, 317))
MAR_CORNERS: tuple[int, int] = (78, 308)

NOSE_TIP = 1
CHIN = 152
FOREHEAD = 10

# Contours for drawing
# fmt: off
RIGHT_EYE_CONTOUR: tuple[int, ...] = (33, 246, 161, 160, 159, 158, 157, 173, 133, 155, 154, 153, 145, 144, 163, 7)
LEFT_EYE_CONTOUR: tuple[int, ...] = (263, 466, 388, 387, 386, 385, 384, 398, 362, 382, 381, 380, 374, 373, 390, 249)
INNER_LIPS_CONTOUR: tuple[int, ...] = (
    78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308, 324, 318, 402, 317, 14, 87, 178, 88, 95,
)
FACE_OVAL: tuple[int, ...] = (
    10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377,
    152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109,
)
# fmt: on
