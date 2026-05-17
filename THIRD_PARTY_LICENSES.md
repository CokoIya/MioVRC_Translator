# Third-Party Licenses

This document lists the open-source libraries used by Mio RealTime Translator and their licenses.

## Runtime Dependencies

### UI Framework

#### CustomTkinter
- **License**: MIT License
- **Copyright**: Copyright (c) 2021 Tom Schimansky
- **URL**: https://github.com/TomSchimansky/CustomTkinter
- **Usage**: User interface framework

### Audio Processing

#### sounddevice
- **License**: MIT License
- **Copyright**: Copyright (c) 2015-2023 Matthias Geier
- **URL**: https://github.com/spatialaudio/python-sounddevice
- **Usage**: Audio input/output

#### PyAudioWPatch
- **License**: MIT License
- **Copyright**: Copyright (c) 2022 s0d3s
- **URL**: https://github.com/s0d3s/PyAudioWPatch
- **Usage**: Windows audio loopback capture

#### NumPy
- **License**: BSD 3-Clause License
- **Copyright**: Copyright (c) 2005-2023, NumPy Developers
- **URL**: https://numpy.org/
- **Usage**: Numerical computing

#### SciPy
- **License**: BSD 3-Clause License
- **Copyright**: Copyright (c) 2001-2023 SciPy Developers
- **URL**: https://scipy.org/
- **Usage**: Audio resampling

#### webrtcvad
- **License**: MIT License
- **Copyright**: Copyright (c) 2016 John Wiseman
- **URL**: https://github.com/wiseman/py-webrtcvad
- **Usage**: Voice activity detection

### Speech Recognition

#### FunASR
- **License**: MIT License
- **Copyright**: Copyright (c) 2022 Alibaba Group
- **URL**: https://github.com/alibaba-damo-academy/FunASR
- **Usage**: Speech recognition engine

#### ModelScope
- **License**: Apache License 2.0
- **Copyright**: Copyright (c) 2022 Alibaba Group
- **URL**: https://github.com/modelscope/modelscope
- **Usage**: Model management and download

#### PyTorch
- **License**: BSD-style License
- **Copyright**: Copyright (c) 2016-2023 Facebook, Inc. and its affiliates
- **URL**: https://pytorch.org/
- **Usage**: Deep learning framework

#### torchaudio
- **License**: BSD 2-Clause License
- **Copyright**: Copyright (c) 2017-2023 Facebook, Inc. and its affiliates
- **URL**: https://github.com/pytorch/audio
- **Usage**: Audio processing for PyTorch

### Translation APIs

#### OpenAI Python SDK
- **License**: Apache License 2.0
- **Copyright**: Copyright (c) 2023 OpenAI
- **URL**: https://github.com/openai/openai-python
- **Usage**: OpenAI API client

#### Anthropic Python SDK
- **License**: MIT License
- **Copyright**: Copyright (c) 2023 Anthropic
- **URL**: https://github.com/anthropics/anthropic-sdk-python
- **Usage**: Anthropic Claude API client

#### Requests
- **License**: Apache License 2.0
- **Copyright**: Copyright (c) 2019 Kenneth Reitz
- **URL**: https://github.com/psf/requests
- **Usage**: HTTP library

### VRChat Integration

#### python-osc
- **License**: Public Domain (Unlicense)
- **Copyright**: Copyright (c) 2014 Attwad
- **URL**: https://github.com/attwad/python-osc
- **Usage**: OSC protocol implementation

## Development Dependencies

### Testing

#### pytest
- **License**: MIT License
- **Copyright**: Copyright (c) 2004-2023 Holger Krekel and others
- **URL**: https://pytest.org/
- **Usage**: Testing framework

#### pytest-cov
- **License**: MIT License
- **Copyright**: Copyright (c) 2010 Meme Dough
- **URL**: https://github.com/pytest-dev/pytest-cov
- **Usage**: Test coverage

#### pytest-mock
- **License**: MIT License
- **Copyright**: Copyright (c) 2014 Bruno Oliveira
- **URL**: https://github.com/pytest-dev/pytest-mock
- **Usage**: Mocking for tests

## Bundled Models

### SenseVoice Small
- **License**: MIT License
- **Copyright**: Copyright (c) 2024 Alibaba Group
- **URL**: https://github.com/FunAudioLLM/SenseVoice
- **Model**: iic/SenseVoiceSmall
- **Usage**: Multilingual speech recognition

## Build Tools

### PyInstaller
- **License**: GPL 2.0 with exception
- **Copyright**: Copyright (c) 2005-2023 PyInstaller Development Team
- **URL**: https://www.pyinstaller.org/
- **Usage**: Application packaging

### Inno Setup
- **License**: Inno Setup License (free for commercial use)
- **Copyright**: Copyright (c) 1997-2023 Jordan Russell
- **URL**: https://jrsoftware.org/isinfo.php
- **Usage**: Windows installer creation

## License Texts

### MIT License

```
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Apache License 2.0

```
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

### BSD 3-Clause License

```
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
```

## Compliance Notes

1. **MIT and Apache 2.0 Licenses**: These licenses are permissive and allow commercial use, modification, and distribution. We comply by including this attribution document.

2. **BSD Licenses**: Similar to MIT, these are permissive licenses. We comply by including copyright notices and disclaimers.

3. **GPL with Exception (PyInstaller)**: PyInstaller's license allows bundling with proprietary applications without making them GPL. We comply by using PyInstaller only as a build tool.

4. **Model Licenses**: The SenseVoice model is licensed under MIT, allowing commercial use with attribution.

## Updating This Document

When adding new dependencies:

1. Check the dependency's license
2. Add it to the appropriate section above
3. Ensure license compatibility with MIT
4. Include copyright and URL information

## Questions?

If you have questions about licensing or compliance, please open an issue on GitHub.

---

**Last Updated**: 2026-05-02
