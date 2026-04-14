## 时间与日期 SOP

涉及时间相关的信息时，必须遵循：

1. **时区转换必须用 shell 算，不要自己算**
   用 execute_shell 执行：
   ```
   python3 -c "from datetime import datetime; from zoneinfo import ZoneInfo; dt = datetime(2026, 4, 12, 13, 0, tzinfo=ZoneInfo('America/Los_Angeles')); print(dt.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M %Z'))"
   ```
2. 默认使用中国标准时间（Asia/Shanghai）
3. 搜到的时间信息，先确认是什么时区，再转换
4. 数字和计算也一样——能用 shell 算的不要脑算
