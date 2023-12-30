from .common import GouraudVertex, TextureVertex, CullMode, FrontFace
from .hw import Gl as HardwareGl, TextureBuffer as HardwareTextureBuffer

Gl = HardwareGl
TextureBuffer = HardwareTextureBuffer
