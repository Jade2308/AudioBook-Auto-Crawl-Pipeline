/**
 * Cloudflare Worker: Telegram Bot Webhook -> GitHub Actions Controller
 * Điều khiển AudioBook Pipeline qua Telegram hoàn toàn miễn phí, 24/7, không cần VPS.
 * 
 * Các biến môi trường cần cấu hình trong Cloudflare Worker (Settings -> Variables):
 * - TELEGRAM_BOT_TOKEN : Token của bot Telegram (ví dụ: 8830333714:AAG...)
 * - TELEGRAM_CHAT_ID   : Chat ID được phép điều khiển (ví dụ: 5218125355)
 * - GITHUB_PAT          : GitHub Personal Access Token (classic hoặc fine-grained với quyền 'repo' / 'actions')
 * - GITHUB_REPO         : "Jade2308/AudioBook-Auto-Crawl-Pipeline"
 */

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("OK - AudioBook Telegram Webhook Worker is running!", { status: 200 });
    }

    try {
      const update = await request.json();
      if (!update.message || !update.message.text) {
        return new Response("OK", { status: 200 });
      }

      const chatId = String(update.message.chat.id);
      const text = update.message.text.trim();
      const allowedChatId = String(env.TELEGRAM_CHAT_ID || "");

      // Kiểm tra bảo mật: Chỉ nhận lệnh từ Chat ID của bạn
      if (allowedChatId && chatId !== allowedChatId) {
        await sendTelegram(env, chatId, "⛔ Bạn không có quyền điều khiển bot này.");
        return new Response("OK", { status: 200 });
      }

      const repo = env.GITHUB_REPO || "Jade2308/AudioBook-Auto-Crawl-Pipeline";
      const token = env.GITHUB_PAT;
      const cmd = text.split(" ")[0].toLowerCase();

      if (cmd === "/start" || cmd === "/help") {
        const helpText = 
          `🤖 <b>AudioBook Pipeline Serverless Controller</b>\n\n` +
          `<b>Danh sách lệnh:</b>\n` +
          `  /run     — Kích hoạt chạy pipeline trên GitHub Actions\n` +
          `  /status  — Xem trạng thái lượt chạy hiện tại\n` +
          `  /stop    — Hủy lượt chạy đang diễn ra\n` +
          `  /help    — Hiển thị hướng dẫn này\n\n` +
          `💡 <i>Hệ thống chạy hoàn toàn tự động trên Cloudflare Worker & GitHub Actions, không cần VPS!</i>`;
        await sendTelegram(env, chatId, helpText);
      } 
      else if (cmd === "/run") {
        if (!token) {
          await sendTelegram(env, chatId, "❌ Lỗi: Chưa cấu hình GITHUB_PAT trong Worker Variables.");
          return new Response("OK", { status: 200 });
        }

        // Gọi GitHub API kích hoạt workflow crawl.yml
        const ghUrl = `https://api.github.com/repos/${repo}/actions/workflows/crawl.yml/dispatches`;
        const res = await fetch(ghUrl, {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${token}`,
            "Accept": "application/vnd.github+json",
            "User-Agent": "AudioBook-Telegram-Worker",
            "Content-Type": "application/json"
          },
          body: JSON.stringify({ ref: "main" })
        });

        if (res.status === 204) {
          await sendTelegram(
            env,
            chatId,
            `🚀 <b>Đã kích hoạt AudioBook Pipeline trên GitHub Actions thành công!</b>\n\n` +
            `• <b>Kho mã nguồn:</b> <code>${repo}</code>\n` +
            `• <b>Nhánh:</b> <code>main</code>\n\n` +
            `Máy ảo GitHub đang bắt đầu tải và đồng bộ các tập truyện lên Google Drive.\n` +
            `Bot sẽ tự động gửi báo cáo từng tập khi hoàn thành!\n\n` +
            `👉 <i>Dùng /status để xem tiến trình.</i>`
          );
        } else {
          const errData = await res.text();
          await sendTelegram(
            env,
            chatId,
            `❌ <b>Không thể kích hoạt GitHub Actions (Mã lỗi ${res.status}):</b>\n<code>${errData.substring(0, 300)}</code>`
          );
        }
      } 
      else if (cmd === "/status") {
        if (!token) {
          await sendTelegram(env, chatId, "❌ Lỗi: Chưa cấu hình GITHUB_PAT trong Worker Variables.");
          return new Response("OK", { status: 200 });
        }

        const ghUrl = `https://api.github.com/repos/${repo}/actions/runs?per_page=3`;
        const res = await fetch(ghUrl, {
          headers: {
            "Authorization": `Bearer ${token}`,
            "Accept": "application/vnd.github+json",
            "User-Agent": "AudioBook-Telegram-Worker"
          }
        });

        if (res.ok) {
          const data = await res.json();
          const runs = data.workflow_runs || [];
          if (runs.length === 0) {
            await sendTelegram(env, chatId, "ℹ️ Chưa có lịch sử chạy nào trên GitHub Actions.");
          } else {
            const latest = runs[0];
            const statusIcon = latest.status === "in_progress" ? "🟡 ĐANG CHẠY" : (latest.conclusion === "success" ? "🟢 THÀNH CÔNG" : "🔴 THẤT BẠI / DỪNG");
            const dateStr = new Date(latest.created_at).toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh" });

            let msg = `📊 <b>TRẠNG THÁI GITHUB ACTIONS:</b>\n\n`;
            msg += `• <b>Lượt chạy gần nhất:</b> #${latest.run_number} (${latest.name})\n`;
            msg += `• <b>Trạng thái:</b> <b>${statusIcon}</b>\n`;
            msg += `• <b>Bắt đầu lúc:</b> <code>${dateStr}</code>\n`;
            msg += `• <b>Nhánh:</b> <code>${latest.head_branch}</code> (${latest.head_sha.substring(0, 7)})\n`;
            msg += `• <b>Chi tiết:</b> <a href="${latest.html_url}">Xem trên GitHub</a>\n`;

            if (latest.status === "in_progress") {
              msg += `\n💡 <i>Pipeline đang tải audio và upload lên Google Drive. Gõ /stop nếu bạn muốn dừng.</i>`;
            } else {
              msg += `\n💡 <i>Gõ /run để bắt đầu quét và tải truyện mới.</i>`;
            }

            await sendTelegram(env, chatId, msg);
          }
        } else {
          await sendTelegram(env, chatId, `❌ Lỗi kiểm tra trạng thái từ GitHub (Mã ${res.status}).`);
        }
      } 
      else if (cmd === "/stop") {
        if (!token) {
          await sendTelegram(env, chatId, "❌ Lỗi: Chưa cấu hình GITHUB_PAT trong Worker Variables.");
          return new Response("OK", { status: 200 });
        }

        // Tìm lượt chạy đang in_progress để cancel
        const ghUrl = `https://api.github.com/repos/${repo}/actions/runs?status=in_progress`;
        const res = await fetch(ghUrl, {
          headers: {
            "Authorization": `Bearer ${token}`,
            "Accept": "application/vnd.github+json",
            "User-Agent": "AudioBook-Telegram-Worker"
          }
        });

        if (res.ok) {
          const data = await res.json();
          const runs = data.workflow_runs || [];
          if (runs.length === 0) {
            await sendTelegram(env, chatId, "ℹ️ Hiện không có tiến trình nào đang chạy để dừng.");
          } else {
            const runToCancel = runs[0];
            const cancelUrl = `https://api.github.com/repos/${repo}/actions/runs/${runToCancel.id}/cancel`;
            const cancelRes = await fetch(cancelUrl, {
              method: "POST",
              headers: {
                "Authorization": `Bearer ${token}`,
                "Accept": "application/vnd.github+json",
                "User-Agent": "AudioBook-Telegram-Worker"
              }
            });

            if (cancelRes.status === 202) {
              await sendTelegram(
                env,
                chatId,
                `🛑 <b>Đã gửi lệnh hủy thành công lượt chạy #${runToCancel.run_number}!</b>\n` +
                `Tiến trình trên GitHub Actions đang được dừng lại.`
              );
            } else {
              await sendTelegram(env, chatId, `⚠️ Không thể hủy lượt chạy #${runToCancel.run_number} (Mã lỗi ${cancelRes.status}).`);
            }
          }
        } else {
          await sendTelegram(env, chatId, `❌ Lỗi khi tìm kiếm tiến trình đang chạy (Mã ${res.status}).`);
        }
      }

      return new Response("OK", { status: 200 });
    } catch (err) {
      console.error("Worker error:", err);
      return new Response("Error: " + err.message, { status: 500 });
    }
  }
};

async function sendTelegram(env, chatId, text) {
  const token = env.TELEGRAM_BOT_TOKEN;
  if (!token) return;
  const url = `https://api.telegram.org/bot${token}/sendMessage`;
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text: text,
      parse_mode: "HTML",
      disable_web_page_preview: true
    })
  });
}
