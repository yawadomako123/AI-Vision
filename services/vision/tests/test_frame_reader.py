import av
import numpy as np
import pytest

from app.frame_reader import FrameReader


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    with av.open(str(path), "w") as out:
        stream = out.add_stream("libx264", rate=15)
        stream.width, stream.height, stream.pix_fmt = 320, 240, "yuv420p"
        for i in range(30):
            img = np.full((240, 320, 3), i * 8, dtype=np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(img, format="bgr24")):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    return str(path)


def test_reads_file_and_goes_online(clip):
    reader = FrameReader("test", clip).start()
    try:
        frame = reader.latest(timeout=5)
        assert frame is not None
        assert frame.image.shape == (240, 320, 3)
        assert reader.status == "online"
    finally:
        reader.stop()


def test_latest_never_returns_same_frame_twice(clip):
    reader = FrameReader("test", clip).start()
    try:
        first = reader.latest(timeout=5)
        second = reader.latest(timeout=5)
        assert first is not None and second is not None
        assert second.index > first.index
    finally:
        reader.stop()


def test_unreachable_stream_goes_offline():
    reader = FrameReader("test", "rtsp://127.0.0.1:1/none").start()
    try:
        # Windows retries refused connections for ~2s before failing.
        assert reader.latest(timeout=6) is None
        assert reader.status == "offline"
        assert reader.reconnects >= 1
    finally:
        reader.stop()
