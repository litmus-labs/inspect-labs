# Inspect Robots device-claim compatibility

The robot advisory-lock protocol in `devices.py` interoperates with the implementation
in Inspect Robots, commit `3c832c34b6c11fa5205ff80ab4947247fedd5eea`:
https://github.com/robocurve/inspect-robots/blob/3c832c34b6c11fa5205ff80ab4947247fedd5eea/src/inspect_robots/_claims.py

It preserves the native lock-path, identity-normalization and filename conventions.
Inspect Labs implements acquisition with fail-closed errors, rather than importing
the private upstream helper. This compatibility is version-pinned, not a promise of
an upstream public API. Upstream license notice follows.

MIT License

Copyright (c) 2026 robocurve

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
