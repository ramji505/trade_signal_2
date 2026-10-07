import logging
import json
from typing import Dict, Any, Optional

try:
    import httpx
except ImportError:
    httpx = None

from config import settings

logger = logging.getLogger("Notifier")
logger.setLevel(logging.INFO)

class TelegramNotifier:
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.enabled = settings.ENABLE_TELEGRAM

    def send_trade_alert(self, signal: Dict[str, Any]) -> bool:
        """Dispatches an institutional decision support card to Telegram with strict Spot vs Premium separation."""
        if not self.enabled or not self.bot_token or not self.chat_id:
            logger.debug("Telegram notification skipped (not enabled or missing credentials).")
            return False

        bias_icon = "🟢" if signal.get("bias") == "BUY_CE" else "🔴"
        action_name = "CALL (CE) SETUP" if signal.get("bias") == "BUY_CE" else "PUT (PE) SETUP"
        strike = signal.get("strike", "NIFTY")
        spot_price = signal.get("spot_price", signal.get("entry_price", 0.0))
        invalidation = signal.get("invalidation_level", 0.0)
        
        # Option Premium Estimation (Assuming typical ATM premium ~ ₹120-130 if not given)
        opt_entry = signal.get("option_entry", 125.0)
        sl_pts = signal.get("sl_pts", settings.DEFAULT_STOP_LOSS_PTS)
        target_pts = signal.get("target_pts", settings.DEFAULT_TARGET_PTS)
        opt_sl = max(10.0, round(opt_entry - sl_pts, 1))
        opt_target = round(opt_entry + target_pts, 1)

        quality = signal.get("quality_score", int(signal.get("confidence_pct", 85)))
        grade = signal.get("grade", "A")
        regime = signal.get("session_regime", "STRONG_TREND")
        confirmations = signal.get("confirmations", [])
        scalper_link = signal.get("scalper_link", "")

        conf_lines = "\n".join([f"  ✓ {c}" for c in confirmations[:5]]) if confirmations else "  ✓ Multi-factor technical confluence"

        card_text = (
            f"{bias_icon} *NIFTY DECISION SUPPORT ALERT: {action_name}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Setup Quality:* `{quality}/100 [Grade {grade}]`\n"
            f"📊 *Market Regime:* `{regime}`\n\n"
            f"📈 *UNDERLYING INDEX TRIGGER (SPOT):*\n"
            f"  • Spot Level: `₹{spot_price:.2f}`\n"
            f"  • 🛡️ Invalidation: `₹{invalidation:.2f}` (Exit if Spot breaches)\n\n"
            f"⚡ *RECOMMENDED OPTION CONTRACT:*\n"
            f"  • Instrument: `{strike}`\n"
            f"  • Option Entry Zone: `₹{opt_entry:.1f}`\n"
            f"  • Stop-Loss (SL): `₹{opt_sl:.1f}` (-{sl_pts:.1f} pts)\n"
            f"  • Target 1: `₹{opt_target:.1f}` (+{target_pts:.1f} pts | 1:{settings.RISK_REWARD_RATIO} R:R)\n"
            f"  • Max Hold Time: `20 Mins (Auto-exit on Theta Decay)`\n"
            f"  • Validity: `8-Minute Signal Countdown`\n\n"
            f"🔍 *Why? Multi-Layer Confirmation:*\n{conf_lines}\n\n"
            f"🚫 *DO NOT EXECUTE IF:*\n"
            f"  • Option bid-ask spread > 0.40%\n"
            f"  • Spot moves past Invalidation Level (₹{invalidation:.2f})\n"
            f"  • Signal age > 8 minutes\n\n"
            f"📱 *Manual Execution Link:*\n{scalper_link if scalper_link else 'Open Groww > Scalper Terminal'}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *Decision Support Engine. Human trader makes final execution decision.*"
        )

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": card_text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False
        }

        try:
            if httpx:
                with httpx.Client(timeout=5.0) as client:
                    response = client.post(url, json=payload)
                    if response.status_code == 200:
                        logger.info(f"Telegram decision alert sent successfully for {strike}.")
                        return True
                    else:
                        logger.error(f"Failed to send Telegram alert: {response.text}")
                        return False
            import urllib.request
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    logger.info(f"Telegram decision alert sent successfully for {strike}.")
                    return True
                return False
        except Exception as e:
            logger.error(f"Exception sending Telegram alert: {e}")
            return False

telegram_notifier = TelegramNotifier()
