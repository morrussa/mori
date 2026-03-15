# mori_live_stream

最基础的直播接入（目前以 **Bilibili 弹幕轮询** 为主），用于把直播间观众消息转成 Mori 的输入。

## Bilibili 弹幕（轮询版）

实现参考：`morettt/my-neuro` 的 `live-2d/js/live/LiveStreamModule.js`（`api.live.bilibili.com/ajax/msg`）。

### 快速测试

```bash
python3 -m mori_live_stream.cli --room-id <你的房间号>
```

### 与 `vtuber.py` 集成

```bash
python3 vtuber.py --bilibili-room-id <你的房间号> --tts
```

> 注意：该接口属于“简单轮询”，不是官方稳定 SDK；若遇到 412/限流，需要更换 UA、增加间隔或改用 WebSocket 弹幕库（后续可升级）。

