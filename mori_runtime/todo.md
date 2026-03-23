Phase 0：先把参数分家。对当前标准 ZipVoice，先别再默认 4/3.0，改成 profile：realtime=4/1.0、balanced=8/1.0、hq=8~10/1.0。
Phase 1：只移植 Lux 的 vocoder，不动你现有 prompt cache 和 zh/ja 路由。这是成本最低、最像 “Lux 清晰度” 的一步。
Phase 2：给 worker 加 model_type=zipvoice|zipvoice_distill，因为你现在 worker 还硬编码在 ZipVoice：mori_runtime/zipvoice_worker.py:67、mori_runtime/zipvoice_worker.py:89。
Phase 3：补 train_zipvoice_distill.py 的 openjtalk 支持后，再蒸馏你的中日双语模型；现在这个脚本还没支持 openjtalk：/home/morusa/dataset/train/ZipVoice/zipvoice/bin/train_zipvoice_distill.py:342、/home/morusa/dataset/train/ZipVoice/zipvoice/bin/train_zipvoice_distill.py:894。
