/**
 * WebTransport over HTTP/3 バインディング実装
 */

#include "webtransport_h3.h"

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <stdexcept>

namespace webtransport {
namespace h3 {

namespace {

// draft-16 Section 6 の "valid UTF-8" を RFC 3629 の well-formed UTF-8 として
// 検査する。overlong 符号化、サロゲート (U+D800..U+DFFF)、U+10FFFF 超、
// 不完全シーケンス、非先頭バイトを拒否する。受信検証 (recv_wt_close_session_cb)
// と送信トリミング (close_session) の両方で使う
bool is_valid_utf8(const uint8_t* data, size_t length) {
  size_t offset = 0;
  while (offset < length) {
    if (data[offset] <= 0x7F) {
      offset += 1;
      continue;
    }
    size_t extra = 0;
    uint32_t min_codepoint = 0;
    uint32_t codepoint = 0;
    if ((data[offset] & 0xE0) == 0xC0) {
      extra = 1;
      min_codepoint = 0x80;
      codepoint = data[offset] & 0x1F;
    } else if ((data[offset] & 0xF0) == 0xE0) {
      extra = 2;
      min_codepoint = 0x800;
      codepoint = data[offset] & 0x0F;
    } else if ((data[offset] & 0xF8) == 0xF0) {
      extra = 3;
      min_codepoint = 0x10000;
      codepoint = data[offset] & 0x07;
    } else {
      return false;
    }
    if (offset + 1 + extra > length) {
      return false;
    }
    for (size_t index = 1; index <= extra; ++index) {
      if ((data[offset + index] & 0xC0) != 0x80) {
        return false;
      }
      codepoint = (codepoint << 6) | (data[offset + index] & 0x3F);
    }
    if (codepoint < min_codepoint || codepoint > 0x10FFFF ||
        (codepoint >= 0xD800 && codepoint <= 0xDFFF)) {
      return false;
    }
    offset += 1 + extra;
  }
  return true;
}

// WT_CLOSE_SESSION の Application Error Message の最大長 (draft-16 Section 6
// の「its length MUST NOT exceed 1024 bytes」)
constexpr size_t kMaxApplicationErrorMessageBytes = 1024;

// 受信側の H3_MESSAGE_ERROR リセット (handle_wt_close_session_error) で使う
// H3_MESSAGE_ERROR (nghttp3.h の公開定数)。WT_CLOSE_SESSION の Application
// Error Message の不正 (1024 バイト超・4 バイト未満の不正な長さ・不正 UTF-8)
// 受信時の CONNECT ストリームのリセットに使う (draft-16 Section 6 の MUST)
constexpr uint64_t kH3MessageError = NGHTTP3_H3_MESSAGE_ERROR;

}  // namespace

// ========== H3Session 実装 ==========

H3Session::H3Session(bool is_server, const H3SessionConfig& config)
    : is_server_(is_server), config_(config) {}

H3Session::~H3Session() {
  if (conn_) {
    nghttp3_conn_del(conn_);
  }
}

H3Session::H3Session(H3Session&& other) noexcept
    : is_server_(other.is_server_),
      config_(std::move(other.config_)),
      conn_(other.conn_),
      events_(std::move(other.events_)),
      stream_buffers_(std::move(other.stream_buffers_)),
      pending_sends_(std::move(other.pending_sends_)),
      pending_datagrams_(std::move(other.pending_datagrams_)),
      pending_headers_(std::move(other.pending_headers_)),
      session_ids_(std::move(other.session_ids_)),
      pending_fin_session_ids_(std::move(other.pending_fin_session_ids_)),
      pending_pre_accept_fin_session_ids_(
          std::move(other.pending_pre_accept_fin_session_ids_)),
      pending_qpack_blocked_fin_stream_ids_(
          std::move(other.pending_qpack_blocked_fin_stream_ids_)),
      pre_accept_fin_accepted_session_ids_(
          std::move(other.pre_accept_fin_accepted_session_ids_)),
      pending_stale_2xx_discard_session_ids_(
          std::move(other.pending_stale_2xx_discard_session_ids_)),
      pending_wt_close_session_error_session_id_(
          other.pending_wt_close_session_error_session_id_),
      accepting_session_id_(other.accepting_session_id_),
      stream_info_(std::move(other.stream_info_)),
      control_stream_id_(other.control_stream_id_),
      qpack_encoder_stream_id_(other.qpack_encoder_stream_id_),
      qpack_decoder_stream_id_(other.qpack_decoder_stream_id_),
      closed_(other.closed_) {
  other.conn_ = nullptr;
}

H3Session& H3Session::operator=(H3Session&& other) noexcept {
  if (this != &other) {
    if (conn_) {
      nghttp3_conn_del(conn_);
    }
    is_server_ = other.is_server_;
    config_ = std::move(other.config_);
    conn_ = other.conn_;
    events_ = std::move(other.events_);
    stream_buffers_ = std::move(other.stream_buffers_);
    pending_sends_ = std::move(other.pending_sends_);
    pending_datagrams_ = std::move(other.pending_datagrams_);
    pending_headers_ = std::move(other.pending_headers_);
    session_ids_ = std::move(other.session_ids_);
    pending_fin_session_ids_ = std::move(other.pending_fin_session_ids_);
    pending_pre_accept_fin_session_ids_ =
        std::move(other.pending_pre_accept_fin_session_ids_);
    pending_qpack_blocked_fin_stream_ids_ =
        std::move(other.pending_qpack_blocked_fin_stream_ids_);
    pre_accept_fin_accepted_session_ids_ =
        std::move(other.pre_accept_fin_accepted_session_ids_);
    pending_stale_2xx_discard_session_ids_ =
        std::move(other.pending_stale_2xx_discard_session_ids_);
    pending_wt_close_session_error_session_id_ =
        other.pending_wt_close_session_error_session_id_;
    accepting_session_id_ = other.accepting_session_id_;
    stream_info_ = std::move(other.stream_info_);
    control_stream_id_ = other.control_stream_id_;
    qpack_encoder_stream_id_ = other.qpack_encoder_stream_id_;
    qpack_decoder_stream_id_ = other.qpack_decoder_stream_id_;
    closed_ = other.closed_;
    other.conn_ = nullptr;
  }
  return *this;
}

std::unique_ptr<H3Session> H3Session::create_client(
    const H3SessionConfig& config) {
  auto session = std::unique_ptr<H3Session>(new H3Session(false, config));
  if (!session->initialize()) {
    return nullptr;
  }
  return session;
}

std::unique_ptr<H3Session> H3Session::create_server(
    const H3SessionConfig& config) {
  auto session = std::unique_ptr<H3Session>(new H3Session(true, config));
  if (!session->initialize()) {
    return nullptr;
  }
  return session;
}

bool H3Session::initialize() {
  nghttp3_callbacks callbacks{};
  callbacks.acked_stream_data = acked_stream_data_cb;
  callbacks.stream_close = stream_close_cb;
  callbacks.recv_data = recv_data_cb;
  callbacks.deferred_consume = deferred_consume_cb;
  callbacks.begin_headers = begin_headers_cb;
  callbacks.recv_header = recv_header_cb;
  callbacks.end_headers = end_headers_cb;
  callbacks.end_stream = end_stream_cb;
  callbacks.stop_sending = stop_sending_cb;
  callbacks.reset_stream = reset_stream_cb;
  callbacks.shutdown = shutdown_cb;
  callbacks.recv_settings2 = recv_settings2_cb;
  callbacks.recv_wt_data = recv_wt_data_cb;
  callbacks.recv_wt_close_session = recv_wt_close_session_cb;

  nghttp3_settings settings;
  nghttp3_settings_default(&settings);
  settings.max_field_section_size = config_.max_field_section_size;
  settings.qpack_max_dtable_capacity = config_.qpack_max_dtable_capacity;
  settings.qpack_blocked_streams = config_.qpack_blocked_streams;

  // WebTransport を有効化
  settings.enable_connect_protocol = 1;
  settings.h3_datagram = 1;
  settings.wt_enabled = 1;

  int rv;
  if (is_server_) {
    rv = nghttp3_conn_server_new(&conn_, &callbacks, &settings,
                                 nghttp3_mem_default(), this);
  } else {
    rv = nghttp3_conn_client_new(&conn_, &callbacks, &settings,
                                 nghttp3_mem_default(), this);
  }

  return rv == 0;
}

size_t H3Session::receive_stream_data(int64_t stream_id,
                                      const std::vector<uint8_t>& data,
                                      bool fin) {
  if (!conn_) {
    return 0;
  }

  // タイムスタンプを 0 にして read_stream2 を使用
  // nghttp3 は WebTransport データストリームのヘッダを自動的にパースし、
  // recv_wt_data コールバックを呼び出す
  nghttp3_ssize consumed = nghttp3_conn_read_stream2(
      conn_, stream_id, data.data(), data.size(), fin ? 1 : 0, 0);

  if (consumed < 0) {
    // WT_CLOSE_SESSION 関連のストリームエラー検知時の分離処理:
    // - NGHTTP3_ERR_H3_MESSAGE_ERROR かつ CONNECT ストリームは、WT_CLOSE_SESSION
    //   の Application Error Message が 1024 バイト超 (または 4 バイト未満の
    //   不正な長さ) の経路 (draft-16 Section 6 の MUST: H3_MESSAGE_ERROR
    //   でのストリームリセット)。コールバックは発火していないため
    //   session_ids_ に残存しており、リセット処理はここで行う。ストリーム
    //   エラーであり接続エラーではないため Error イベントは積まない
    // - recv_wt_close_session_cb で検知した不正 UTF-8 は保留され、コールバック
    //   の非 0 戻りが NGHTTP3_ERR_CALLBACK_FAILURE として返る。リセット処理は
    //   保留したセッション ID で行い、接続エラーの通知 (Error イベント) は
    //   汎用の負値分岐と同じ挙動で積む (ストリームエラーが接続エラー風に
    //   通知されるが、高レベル層は接続クローズに直接作用しないため許容)
    bool h3_message_error_reset = false;
    if (consumed == NGHTTP3_ERR_H3_MESSAGE_ERROR &&
        session_ids_.count(stream_id) > 0) {
      handle_wt_close_session_error(stream_id);
      h3_message_error_reset = true;
    }
    if (pending_wt_close_session_error_session_id_.has_value()) {
      int64_t pending_session_id = *pending_wt_close_session_error_session_id_;
      pending_wt_close_session_error_session_id_.reset();
      handle_wt_close_session_error(pending_session_id);
    }
    if (!h3_message_error_reset) {
      H3Event event;
      event.type = H3EventType::Error;
      event.stream_id = stream_id;
      event.error_code = static_cast<uint64_t>(-consumed);
      event.error_message = nghttp3_strerror(static_cast<int>(consumed));
      push_event(std::move(event));
    }
  }

  // 受理前 FIN の検知 (サーバー側の CONNECT ストリームに限定)。
  // 受理前 FIN (サーバーが応答を送信する前に CONNECT ストリームが FIN で
  // 閉じられた) では、nghttp3 がストリームを WT_SESSION_BLOCKED にして
  // 空 FIN を処理しないため end_stream コールバックが発火せず、既存の
  // FIN 経路 (end_stream コールバックによる検知) では検知できない。
  // ここでは receive_stream_data に渡る fin 引数で直接検知する。
  // end_stream が発火した受理後 FIN は pending_fin_session_ids_ に記録済み
  // のため対象外とし、二重処理を回避する (判定は pending_fin_session_ids_
  // の clear より前に行う)。判定を read_stream2 の後に置くことで、同一
  // 読み取り (ヘッダー + FIN) でも end_headers_cb による session_ids_ への
  // 挿入完了後に検知できる。
  if (fin && is_server_ && session_ids_.count(stream_id) > 0 &&
      pending_fin_session_ids_.count(stream_id) == 0) {
    pending_pre_accept_fin_session_ids_.insert(stream_id);
  }

  // QPACK デコードブロック中の受理前 FIN の検知 (サーバー側の CONNECT
  // ストリームに限定)。上記の検知はヘッダー処理完了 (= end_headers_cb
  // 実行済み) に依存し、ヘッダーが QPACK デコードブロック中に fin 付き
  // データが届くとヘッダー未処理のため検知が成立しない。
  //
  // ブロック中の nghttp3 の挙動: ブロック中のデータは inq にバッファされ、
  // ブロック解除後の再処理 (process_blocked_stream_data) で inq の最後の
  // チャンクに READ_EOF が fin として伝播される (フィールドセクションのみ
  // がバッファされる場合)。しかし read_bidi がヘッダー完了後に「Server has
  // not submitted response」の分岐で WT_SESSION_BLOCKED を立てて早期
  // return するため、almost_done の fin 処理 (end_stream コールバック) に
  // 到達せず fin は喪失する (フィールドセクションに後続データが混在する
  // 読み取りは nghttp3 側の既知の異常挙動のため対象外)。
  //
  // ここでは fin 引数で「fin が渡ったが session_ids_ に未挿入 (ヘッダー未
  // 処理) かつ pending_headers_ に含まれる (begin_headers_cb 発火済み・
  // end_headers_cb 未発火) ストリーム」を保留集合に一時記録し、ブロック
  // 解除後の読み取りで CONNECT 判定 (session_ids_ への挿入) を確認して
  // pending_pre_accept_fin_session_ids_ へ移行する (下記の移行処理)。
  // 記録条件は上記の検知条件 (count > 0) と排他 (count == 0) であり、
  // QPACK ブロックなしの同一読み取り (ヘッダー + FIN) は上記で検知される
  // ため二重検知しない。pending_headers_ のメンバーシップで CONNECT 以外の
  // ストリーム (WT データストリーム・ヘッダー処理済みの通常リクエスト・
  // 制御ストリーム等) の FIN を記録から除外する。セッション確定に至らな
  // かったストリームの記録は end_headers_cb で、ブロック中にリセットされた
  // ストリームの記録は close_stream で除去する (どちらも除去できない場合
  // は接続終了まで残留する既知の制約)。クライアント側 (is_server_ ==
  // false) は受理前 FIN の概念がないため対象外
  if (fin && is_server_ && session_ids_.count(stream_id) == 0 &&
      pending_headers_.count(stream_id) > 0) {
    pending_qpack_blocked_fin_stream_ids_.insert(stream_id);
  }

  // QPACK ブロック解除後に CONNECT 判定された保留ストリームを
  // pending_pre_accept_fin_session_ids_ へ移行する。移行は
  // nghttp3_conn_read_stream2 から戻った後 (end_headers_cb による
  // session_ids_ 挿入・pending_headers_ 削除の後) に行うため、アプリが
  // SESSION_READY を受けて accept_session を呼ぶ時点では既に移行済みで、
  // accept_session 内の既存の移行処理 (pending_pre_accept_fin_session_ids_
  // のメンバーシップ確認) がそのまま機能する。session_ids_ に含まれない
  // 保留ストリーム (まだブロック中・CONNECT 判定されなかった) はそのまま
  // 残す
  for (auto it = pending_qpack_blocked_fin_stream_ids_.begin();
       it != pending_qpack_blocked_fin_stream_ids_.end();) {
    if (session_ids_.count(*it) > 0) {
      pending_pre_accept_fin_session_ids_.insert(*it);
      it = pending_qpack_blocked_fin_stream_ids_.erase(it);
    } else {
      ++it;
    }
  }

  // end_stream コールバックで検知した CONNECT ストリームの FIN を処理する。
  // コールバック内で nghttp3 を再度呼ぶと再入になるため、検知したセッション
  // ID は保留集合への記録だけに留め、nghttp3_conn_read_stream2 から戻った
  // この時点で close_stream によりセッション終了の後始末を行う (詳細は
  // end_stream_cb のコメント参照)。read_stream2 がエラーを返した場合も
  // 保留集合をそのまま処理する (エラー由来の Error イベントと SessionClosed
  // が並ぶ可能性は許容する)。close_stream の nghttp3_conn_close_stream
  // 呼び出しは nghttp3 内部でセッションに属するデータストリームを
  // WT_SESSION_GONE で破棄するため、draft-ietf-webtrans-http3-16 Section 6
  // の MUST (セッション終了時に属するストリームを WT_SESSION_GONE でリセット
  // し、新しいデータグラム・ストリームを開かない) が満たされる
  for (int64_t session_id : pending_fin_session_ids_) {
    // FIN 経路の error_code は 0 とする (WT_CLOSE_SESSION 無しの
    // クリーンクローズは error code 0 かつ空のエラー文字列の
    // WT_CLOSE_SESSION と等価。draft-ietf-webtrans-http3-16 Section 6)
    close_stream(session_id, 0);
  }
  pending_fin_session_ids_.clear();

  // 遅延クローズ保留中に WT_CLOSE_SESSION を受信したセッションの未送信
  // 2xx を破棄する。close_stream は read_stream2 から戻ったこの時点で
  // 実行する (コールバック内での再入防止。read_stream2 がエラーを返した
  // 場合も保留集合をそのまま処理する)。詳細は discard_stale_2xx の実装
  // コメント。accept_session が confirm から戻った直後に同処理を実行済み
  // の場合は保留集合が空のため何もしない (二重 close_stream は発生しない)
  discard_stale_2xx();

  if (consumed < 0) {
    return 0;
  }

  return static_cast<size_t>(consumed);
}

void H3Session::receive_datagram(const std::vector<uint8_t>& data) {
  // WebTransport データグラムは先頭に Quarter Stream ID (varint) が付く
  // 仕様: draft-ietf-webtrans-http3 / RFC 9000 可変長整数
  if (data.empty()) {
    return;
  }

  size_t varint_len = nghttp3_get_uvarintlen(data.data());
  if (varint_len == 0 || varint_len > data.size()) {
    return;
  }

  uint64_t quarter_stream_id = 0;
  nghttp3_get_uvarint(&quarter_stream_id, data.data());

  // セッション ID = Quarter Stream ID * 4。セッション ID はクライアント起動
  // 双方向ストリーム ID に対応する必要があり (draft-ietf-webtrans-http3-16
  // Section 4)、QUIC ストリーム ID の範囲 (RFC 9000 Section 2.1 の
  // 2^62-1 まで) を超えるセッション ID は H3_ID_ERROR (RFC 9114 の
  // アプリケーションエラーコード 0x0108) で接続を閉じる MUST の対象。
  // 構造検証は閉じたセッションの ID を検査対象外とする (Section 4 の
  // "Session IDs that correspond to closed sessions are not considered
  // invalid for the purposes of this check")。配信の要否は下記の終了状態
  // 確認が担う
  constexpr uint64_t max_session_id = (1ULL << 62) - 1;
  uint64_t session_id = quarter_stream_id * 4;
  if (session_id > max_session_id) {
    H3Event event;
    event.type = H3EventType::Error;
    event.error_code = 0x0108;  // H3_ID_ERROR
    event.error_message = "invalid session ID in datagram";
    push_event(std::move(event));
    return;
  }

  // セッション終了を学習したエンドポイントは、終了したセッション ID 宛の
  // データグラムをアプリに配信しない (実装ポリシー。根拠は
  // draft-ietf-webtrans-http3-16 Section 4 の「closed session 宛のデータの
  // 扱いは Section 6 に従う (endpoints handle data for closed sessions as
  // described in Section 6)」と、データグラムは再送されず配信保証がない
  // こと (Section 4.1 / RFC 9221))。受信データストリームの破棄
  // (recv_wt_data_cb) と同じ方針で、session_ids_ のメンバーシップ確認と
  // 受理前 FIN 検知済み集合 (pending_pre_accept_fin_session_ids_ /
  // pre_accept_fin_accepted_session_ids_) の確認を行う。受理前 FIN 検知
  // 済みセッションの確認は、データグラム経路では nghttp3 のバッファリング
  // が無く 2xx 書き出し完了まで session_ids_ に残るため必須である。
  // 一度も確立されていないセッション ID 宛も破棄される (send_datagram の
  // 送信側と同じ意味論)。範囲チェックの後に行う (範囲外 ID は先に
  // H3_ID_ERROR で接続を閉じる。既存の構造検証の挙動維持)。楽観的送受信
  // は妨げない: クライアントは connect 直後に、サーバーは CONNECT リクエ
  // スト受信時 (end_headers_cb) に session_ids_ へ挿入される。ただし
  // サーバー側は CONNECT リクエストの処理完了前 (QPACK デコードブロック中
  // を含む。end_headers_cb 未実行) に届いたデータグラムのみ破棄される
  // (QUIC はデータグラムとストリームデータの到着順序を保証しないため、
  // クライアントの楽観的データグラムが CONNECT より先行到着した場合に
  // 喪失し得る。データグラムは再送されず喪失は無害なため、draft
  // Section 4.6 の SHOULD バッファリングに対する許容された逸脱)。
  // サーバー側の reject_session (非 2xx 拒否) は session_ids_ から削除
  // するため、拒否されたセッション ID 宛のデータグラムは破棄される
  // (Origin 検証失敗の内部 403 経路は session_ids_ への挿入前のため同様)
  if (session_ids_.count(static_cast<int64_t>(session_id)) == 0 ||
      pending_pre_accept_fin_session_ids_.count(
          static_cast<int64_t>(session_id)) > 0 ||
      pre_accept_fin_accepted_session_ids_.count(
          static_cast<int64_t>(session_id)) > 0) {
    return;
  }

  H3Event event;
  event.type = H3EventType::Datagram;
  event.session_id = static_cast<int64_t>(session_id);
  event.data.assign(data.begin() + static_cast<std::ptrdiff_t>(varint_len),
                    data.end());
  push_event(std::move(event));
}

std::vector<std::tuple<int64_t, std::vector<uint8_t>, bool>>
H3Session::get_streams_to_send() {
  std::vector<std::tuple<int64_t, std::vector<uint8_t>, bool>> result;

  if (!conn_) {
    return result;
  }

  // nghttp3 からデータを読み出す
  // 無限ループ防止のため上限を設ける
  constexpr int max_iterations = 1024;
  for (int iteration = 0; iteration < max_iterations; ++iteration) {
    nghttp3_vec vec[8];
    int64_t stream_id = -1;
    int fin = 0;

    nghttp3_ssize sveccnt =
        nghttp3_conn_writev_stream(conn_, &stream_id, &fin, vec, 8);

    if (sveccnt < 0) {
      break;
    }

    if (stream_id < 0) {
      break;
    }

    // データをコピー
    std::vector<uint8_t> data;
    for (nghttp3_ssize i = 0; i < sveccnt; ++i) {
      data.insert(data.end(), vec[i].base, vec[i].base + vec[i].len);
    }

    if (!data.empty() || fin) {
      result.emplace_back(stream_id, std::move(data), fin != 0);
    }

    // 送信済みを通知
    size_t total = 0;
    for (nghttp3_ssize i = 0; i < sveccnt; ++i) {
      total += vec[i].len;
    }
    // 進捗がない場合は打ち切る (WOULDBLOCK 相当)
    if (total == 0 && fin == 0) {
      break;
    }
    if (total > 0) {
      nghttp3_conn_add_write_offset(conn_, stream_id, total);
      // 書き出したデータを nghttp3 の送信バッファから解放する
      // QUIC (ngtcp2) が再送用データを保持するため、ACK を待たずに
      // 解放してよい。この呼び出しで acked_stream_data コールバックが
      // 発火し、stream_buffers_ が解放される
      nghttp3_conn_add_ack_offset(conn_, stream_id, total);
    } else if (fin) {
      // FIN のみの場合も offset 0 を通知する
      nghttp3_conn_add_write_offset(conn_, stream_id, 0);
      nghttp3_conn_add_ack_offset(conn_, stream_id, 0);
    }
  }

  // 受理前 FIN を検知して受理済みのセッションを、2xx レスポンスの書き出し
  // 完了後に close_stream で後始末する。未送信の 2xx を破棄しないため、
  // stream_flushed で書き出し完了を確認してから実行する (accept_session 直後
  // は 0、get_streams_to_send で書き出し後に 1 になる。存在しないストリーム
  // も 1 を返すため、受理済み集合との組み合わせで判定する)。書き出しが
  // 完了しない間 (フロー制御等) はセッション ID が残るが、送信は
  // send_datagram / open_stream で拒否済みのため実害はない
  for (auto it = pre_accept_fin_accepted_session_ids_.begin();
       it != pre_accept_fin_accepted_session_ids_.end();) {
    int64_t session_id = *it;
    if (nghttp3_conn_is_stream_flushed(conn_, session_id) == 1) {
      it = pre_accept_fin_accepted_session_ids_.erase(it);
      close_stream(session_id, 0);
    } else {
      ++it;
    }
  }

  return result;
}

std::vector<std::vector<uint8_t>> H3Session::get_datagrams_to_send() {
  std::vector<std::vector<uint8_t>> result(pending_datagrams_.begin(),
                                           pending_datagrams_.end());
  pending_datagrams_.clear();
  return result;
}

void H3Session::bind_control_stream(int64_t stream_id) {
  if (!conn_) {
    return;
  }
  // ストリーム ID の検証
  // QUIC varint の最大値チェック
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return;
  }
  // 単方向ストリームかチェック (クライアント: %4==2, サーバー: %4==3)
  if (is_server_) {
    if (stream_id % 4 != 3) {
      return;
    }
  } else {
    if (stream_id % 4 != 2) {
      return;
    }
  }
  control_stream_id_ = stream_id;
  nghttp3_conn_bind_control_stream(conn_, stream_id);
}

void H3Session::bind_qpack_encoder_stream(int64_t stream_id) {
  if (!conn_) {
    return;
  }
  // ストリーム ID の検証
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return;
  }
  // 単方向ストリームかチェック (クライアント: %4==2, サーバー: %4==3)
  if (is_server_) {
    if (stream_id % 4 != 3) {
      return;
    }
  } else {
    if (stream_id % 4 != 2) {
      return;
    }
  }
  qpack_encoder_stream_id_ = stream_id;
  // 両方のストリーム ID が有効な場合のみバインドする
  if (qpack_decoder_stream_id_ >= 0) {
    nghttp3_conn_bind_qpack_streams(conn_, qpack_encoder_stream_id_,
                                    qpack_decoder_stream_id_);
  }
}

void H3Session::bind_qpack_decoder_stream(int64_t stream_id) {
  if (!conn_) {
    return;
  }
  // ストリーム ID の検証
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return;
  }
  // 単方向ストリームかチェック (クライアント: %4==2, サーバー: %4==3)
  if (is_server_) {
    if (stream_id % 4 != 3) {
      return;
    }
  } else {
    if (stream_id % 4 != 2) {
      return;
    }
  }
  qpack_decoder_stream_id_ = stream_id;
  if (qpack_encoder_stream_id_ >= 0) {
    nghttp3_conn_bind_qpack_streams(conn_, qpack_encoder_stream_id_,
                                    qpack_decoder_stream_id_);
  }
}

bool H3Session::connect(int64_t stream_id,
                        const std::string& url,
                        const std::string& origin) {
  if (!conn_ || is_server_) {
    return false;
  }
  // QPACK ストリームがバインドされていない場合は false を返す
  // nghttp3 は tx.qenc が設定されていることを assert する
  if (qpack_encoder_stream_id_ < 0 || qpack_decoder_stream_id_ < 0) {
    return false;
  }
  // ストリーム ID の検証
  // クライアント起動の双方向ストリームである必要がある (stream_id % 4 == 0)
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint || stream_id % 4 != 0) {
    return false;
  }

  // URL をパース
  std::string authority;
  std::string path;

  // 簡易的な URL パース
  size_t scheme_end = url.find("://");
  if (scheme_end != std::string::npos) {
    size_t host_start = scheme_end + 3;
    size_t path_start = url.find('/', host_start);
    if (path_start != std::string::npos) {
      authority = url.substr(host_start, path_start - host_start);
      path = url.substr(path_start);
    } else {
      authority = url.substr(host_start);
      path = "/";
    }
  } else {
    return false;
  }

  // WebTransport CONNECT リクエストヘッダー
  // ヘッダー名は静的文字列リテラルを使用
  static const char* header_method = ":method";
  static const char* header_scheme = ":scheme";
  static const char* header_authority = ":authority";
  static const char* header_path = ":path";
  static const char* header_protocol = ":protocol";
  static const char* header_origin = "origin";

  std::string method = "CONNECT";
  std::string scheme = "https";
  std::string protocol = "webtransport-h3";

  std::vector<nghttp3_nv> nva = {
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_method)),
       reinterpret_cast<uint8_t*>(const_cast<char*>(method.data())),
       strlen(header_method), method.size(), NGHTTP3_NV_FLAG_NONE},
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_scheme)),
       reinterpret_cast<uint8_t*>(const_cast<char*>(scheme.data())),
       strlen(header_scheme), scheme.size(), NGHTTP3_NV_FLAG_NONE},
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_authority)),
       reinterpret_cast<uint8_t*>(authority.data()), strlen(header_authority),
       authority.size(), NGHTTP3_NV_FLAG_NONE},
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_path)),
       reinterpret_cast<uint8_t*>(path.data()), strlen(header_path),
       path.size(), NGHTTP3_NV_FLAG_NONE},
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_protocol)),
       reinterpret_cast<uint8_t*>(const_cast<char*>(protocol.data())),
       strlen(header_protocol), protocol.size(), NGHTTP3_NV_FLAG_NONE},
  };

  // draft-ietf-webtrans-http3-16 Section 3.2: 非ブラウザクライアントでは
  // OPTIONAL。将来改訂される可能性がある
  if (!origin.empty()) {
    nva.push_back({reinterpret_cast<uint8_t*>(const_cast<char*>(header_origin)),
                   reinterpret_cast<uint8_t*>(const_cast<char*>(origin.data())),
                   strlen(header_origin), origin.size(), NGHTTP3_NV_FLAG_NONE});
  }

  // WebTransport 専用の submit 関数を使用
  int rv = nghttp3_conn_submit_wt_request(conn_, stream_id, nva.data(),
                                          nva.size(), nullptr);
  if (rv != 0) {
    return false;
  }

  // セッション ID を記録
  session_ids_.insert(stream_id);

  return true;
}

bool H3Session::accept_session(int64_t stream_id) {
  if (!conn_ || !is_server_) {
    return false;
  }
  // QPACK ストリームがバインドされていない場合は false を返す
  // nghttp3 は tx.qenc が設定されていることを assert する
  if (qpack_encoder_stream_id_ < 0 || qpack_decoder_stream_id_ < 0) {
    return false;
  }

  // session_ids_ に含まれないセッション (reject_session の非 2xx 拒否で
  // 削除済み・close_session / WT_CLOSE_SESSION 受信で終了済み・存在しない
  // ID 等) は受理不可能のため false を返す (誤用の明示)。サーバー側の
  // session_ids_ への挿入は end_headers_cb が SESSION_READY の発火より前
  // に行うため、正常フロー (SESSION_READY 受信 → accept_session) の
  // セッションは必ず含まれ、影響しない。非 2xx で拒否されたセッションは
  // 一度も確立されていない (draft-ietf-webtrans-http3-16 Section 3.2 の
  // 「サーバーの視点では、2xx 応答を送信した時点でセッションが確立される」)
  // ため、誤用経路で submit / confirm に進むと受理前にバッファされた
  // WT_CLOSE_SESSION カプセルが処理されて SessionClosed が発火し、終了通知
  // の意味論に反する (バッファされたカプセルの処理は confirm 時のみ。
  // 下記の confirm 処理の説明を参照)。ガードにより submit / confirm に
  // 進まないため、カプセルは処理されず SessionClosed も積まれず、拒否時に
  // reject_session が submit した非 2xx 応答はそのまま送出される (誤用時も
  // 拒否の通知が失われない。draft-ietf-webtrans-http3-16 Section 3.2 の
  // 「受理前に届いたカプセルは受理されれば処理され、拒否されれば破棄される」)
  if (session_ids_.count(stream_id) == 0) {
    return false;
  }

  // 200 OK レスポンス
  // ヘッダー名と値は静的文字列リテラルを使用
  static const char* header_status = ":status";
  static const char* value_status = "200";
  static const char* header_draft = "sec-webtransport-http3-draft";
  static const char* value_draft = "draft02";

  std::vector<nghttp3_nv> nva = {
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_status)),
       reinterpret_cast<uint8_t*>(const_cast<char*>(value_status)),
       strlen(header_status), strlen(value_status), NGHTTP3_NV_FLAG_NONE},
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_draft)),
       reinterpret_cast<uint8_t*>(const_cast<char*>(value_draft)),
       strlen(header_draft), strlen(value_draft), NGHTTP3_NV_FLAG_NONE},
  };

  // WebTransport セッション用のレスポンスを送信
  // nghttp3_conn_submit_wt_response を使用 (end_headers コールバック外なので)
  int rv =
      nghttp3_conn_submit_wt_response(conn_, stream_id, nva.data(), nva.size());
  if (rv != 0) {
    return false;
  }

  // 受理前 FIN を検知済みのセッションは、2xx レスポンスの書き出し完了後に
  // close_stream で後始末する (get_streams_to_send 内で stream_flushed を
  // 確認してから実行)。受理前の close_stream は submit_wt_response が
  // NGHTTP3_ERR_STREAM_NOT_FOUND になり、クライアントがセッション確立を
  // 認識できなくなるため、受理後の遅延処理が必要。
  //
  // 移行は confirm より前に行う: 受理前にバッファされた WT_CLOSE_SESSION
  // カプセルは confirm の処理中 (process_blocked_wt_stream_data) に同期
  // 処理されて recv_wt_close_session_cb が発火する (draft-ietf-webtrans
  // -http3-16 Section 3.2 の「A server MUST NOT process these bytes as
  // capsules until it sends a 2xx response accepting the session」)。
  // その時点で移行済み (pre_accept_fin_accepted_session_ids_ に含まれる) に
  // しておくと、recv_wt_close_session_cb の破棄記録 (未送信 2xx の破棄)
  // が成立する (confirm 失敗時の戻しは下記の失敗分岐を参照)
  bool pre_accept_fin_detected = false;
  if (pending_pre_accept_fin_session_ids_.count(stream_id) > 0) {
    pre_accept_fin_detected = true;
    pending_pre_accept_fin_session_ids_.erase(stream_id);
    pre_accept_fin_accepted_session_ids_.insert(stream_id);
  }

  // WebTransport セッションを確認済みとしてマーク
  // end_headers コールバック外で submit_wt_response を呼んだ場合は必須。
  // 上記のとおり confirm の処理中に recv_wt_close_session_cb が発火し得る。
  // 発火時の破棄記録条件 (recv_wt_close_session_cb の判定) に使うため、
  // 処理中のセッション ID を記録しておく (処理後は必ず -1 に戻す)
  accepting_session_id_ = stream_id;
  rv = nghttp3_conn_server_confirm_wt_session(conn_, stream_id, UINT64_MAX);
  accepting_session_id_ = -1;
  if (rv != 0) {
    // 移行済みエントリを pending に戻す: 除去すると終了学習済みセッション
    // への送信ブロック (send_datagram / open_stream のメンバーシップ確認)
    // が解除され、draft-ietf-webtrans-http3-16 Section 6 の MUST (終了を
    // 学習したエンドポイントは新しいデータグラム・ストリームを送信しない)
    // に反する窓が開くため。recv_wt_close_session_cb が発火済みの場合は
    // 破棄記録もここで処理する (confirm 成功時と同じく 2xx の書き出し前に
    // 破棄する。未記録なら no-op)
    if (pre_accept_fin_detected) {
      pre_accept_fin_accepted_session_ids_.erase(stream_id);
      pending_pre_accept_fin_session_ids_.insert(stream_id);
    }
    // confirm の処理中に受理前バッファの WT_CLOSE_SESSION が処理されて不正と
    // 判定された場合のリセット処理:
    // - 不正 UTF-8 は recv_wt_close_session_cb 内で検知され保留される
    //   (コールバックの非 0 戻りが NGHTTP3_ERR_CALLBACK_FAILURE として
    //   confirm の失敗へ合流する)
    // - 1024 バイト超 (および 4 バイト未満の不正な長さ) のカプセルは
    //   コールバック非発火のため保留は未設定で、confirm 自体が
    //   NGHTTP3_ERR_H3_MESSAGE_ERROR で失敗する
    // どちらも draft-16 Section 6 の MUST (H3_MESSAGE_ERROR でのリセット) を
    // 満たすため、当該セッション ID でリセット処理を実行する
    if (rv == NGHTTP3_ERR_H3_MESSAGE_ERROR ||
        pending_wt_close_session_error_session_id_.has_value()) {
      int64_t error_session_id =
          pending_wt_close_session_error_session_id_.value_or(stream_id);
      pending_wt_close_session_error_session_id_.reset();
      handle_wt_close_session_error(error_session_id);
    }
    discard_stale_2xx();
    return false;
  }

  // セッション ID は CONNECT リクエスト受信時 (end_headers_cb) に挿入
  // 済みのため、ここでは再挿入しない。confirm の処理中に
  // recv_wt_close_session_cb が発火して session_ids_ から削除された
  // セッションを再挿入すると、削除済みセッションが復活して
  // send_datagram / open_stream の窓が開き、遅延クローズとの SessionClosed
  // 二重発火も残る

  // confirm の処理中に受理前バッファの WT_CLOSE_SESSION が処理されて破棄
  // 記録されたセッションの未送信 2xx を破棄する。receive_stream_data の
  // 後段の破棄処理は read_stream2 からの復帰後にしか実行されないため、
  // そのままでは accept_session 直後に呼ばれる get_streams_to_send が 2xx
  // を先に書き出してしまう。accept_session はアプリ呼び出しであり nghttp3
  // コールバック内ではないため、ここで直接 close_stream できる。ここで
  // 処理されるのは confirm 経由の記録のみであり、遅延クローズ経由の記録
  // (receive_stream_data の後段) は read_stream2 からの復帰時に処理済みの
  // ため、二重の close_stream は発生しない
  discard_stale_2xx();

  return true;
}

void H3Session::reject_session(int64_t stream_id, int status_code) {
  if (!conn_ || !is_server_) {
    return;
  }
  // QPACK ストリームがバインドされていない場合は何もしない
  // nghttp3 は tx.qenc が設定されていることを assert する
  if (qpack_encoder_stream_id_ < 0 || qpack_decoder_stream_id_ < 0) {
    return;
  }

  // ヘッダー名は静的文字列リテラルを使用
  static const char* header_status = ":status";
  // ステータスコード値は submit_response 呼び出し中有効である必要がある
  std::string status_value = std::to_string(status_code);

  std::vector<nghttp3_nv> nva = {
      {reinterpret_cast<uint8_t*>(const_cast<char*>(header_status)),
       reinterpret_cast<uint8_t*>(status_value.data()), strlen(header_status),
       status_value.size(), NGHTTP3_NV_FLAG_NONE},
  };

  // submit_response の成否を確認する (失敗時は未送出のまま)
  (void)nghttp3_conn_submit_response(conn_, stream_id, nva.data(), nva.size(),
                                     nullptr);

  // 非 2xx 応答で拒否されたセッションは一度も確立されていない
  // (draft-ietf-webtrans-http3-16 Section 3.2) ため、SessionClosed は発火
  // しない (黙って削除)。削除により、以後の send_datagram / open_stream /
  // receive_datagram がメンバーシップ確認で塞がれる。受理前 FIN 検知済み
  // セッション (pending_pre_accept_fin_session_ids_) のエントリも除去する
  // (拒否されたセッションは accept_session による移行が発生しないため、
  // 除去しないと残留する)。2xx を渡した場合は削除しない (2xx 送出は
  // Section 3.2 の確立条件。受理前 FIN 検知済みセッションに 2xx を渡した
  // 場合は pending 集合のエントリが残り、送信がブロックされ続ける)。
  // accept_session で受理済みのセッションに呼んだ場合は未定義 (誤用)
  if (status_code / 100 != 2) {
    session_ids_.erase(stream_id);
    pending_pre_accept_fin_session_ids_.erase(stream_id);
  }
}

bool H3Session::verify_origin(
    const std::vector<std::pair<std::string, std::string>>& headers) const {
  // 許可リストが空 (未設定) の場合は従来どおり全オリジンを受理する
  if (config_.allowed_origins.empty()) {
    return true;
  }

  // Origin ヘッダーが無いリクエストは受理する
  // (draft-ietf-webtrans-http3-16 Section 3.2: 非ブラウザクライアントでは
  // OPTIONAL)
  std::string origin;
  bool has_origin = false;
  bool multiple_origins = false;
  for (const auto& header : headers) {
    if (header.first == "origin") {
      if (has_origin) {
        multiple_origins = true;
      }
      has_origin = true;
      origin = header.second;
    }
  }

  // 複数・空値の Origin は検証失敗として扱う (RFC 6454 の serialized
  // origin は単一かつ非空)
  if (multiple_origins || (has_origin && origin.empty())) {
    return false;
  }

  if (!has_origin) {
    return true;
  }

  for (const auto& allowed_origin : config_.allowed_origins) {
    if (origin == allowed_origin) {
      return true;
    }
  }
  return false;
}

// WebTransport データストリーム用の read_data コールバック
// stream_buffers_ からデータを取得して返す
static nghttp3_ssize wt_data_read_callback(nghttp3_conn* /*conn*/,
                                           int64_t stream_id,
                                           nghttp3_vec* vec,
                                           size_t veccnt,
                                           uint32_t* pflags,
                                           void* conn_user_data,
                                           void* /*stream_user_data*/) {
  if (veccnt == 0 || !conn_user_data) {
    return NGHTTP3_ERR_WOULDBLOCK;
  }

  auto* session = static_cast<H3Session*>(conn_user_data);
  return session->read_data_callback(stream_id, vec, veccnt, pflags);
}

bool H3Session::open_stream(int64_t session_id,
                            int64_t stream_id,
                            bool is_unidirectional) {
  if (!conn_) {
    return false;
  }

  // QUIC varint の最大値チェック
  constexpr int64_t max_varint = (1LL << 62) - 1;
  if (stream_id < 0 || stream_id > max_varint) {
    return false;
  }
  if (session_id < 0 || session_id > max_varint) {
    return false;
  }

  // セッション終了を学習したエンドポイントは新しいストリームを開いては
  // ならない (draft-ietf-webtrans-http3-16 Section 6 の MUST 「it MUST NOT
  // send any new datagrams or open any new streams」)。close_session
  // (WT_CLOSE_SESSION 送出) と recv_wt_close_session_cb (WT_CLOSE_SESSION
  // 受信) は session_ids_ からセッション ID を削除するが、nghttp3 の
  // CONNECT ストリームはストリームテーブルに残存し wt.session も解放され
  // ないため、現在の依存 nghttp3 でも close_wt_session 後の
  // open_wt_data_stream は成功し得る。session_ids_ のメンバーシップ確認で
  // 終了したセッション ID へのストリーム開放を実効的に拒否する (WT_CLOSE
  // _SESSION 受信後は nghttp3 側でも拒否されるが、全経路を一貫して拒否
  // する)。受理前 FIN を検知したセッション (終了を学習済みだが
  // close_stream による後始末前。この間は session_ids_ に含まれたまま)
  // も同様に拒否する。close_stream による CONNECT ストリームのクローズ
  // 経路は、nghttp3 側でも CONNECT ストリームが削除されて
  // open_wt_data_stream が失敗する。楽観的オープン
  // (draft-ietf-webtrans-http3-16 Section 4) は妨げない: クライアントは
  // connect 直後に session_ids_ へ挿入されるため
  if (session_ids_.count(session_id) == 0 ||
      pending_pre_accept_fin_session_ids_.count(session_id) > 0 ||
      pre_accept_fin_accepted_session_ids_.count(session_id) > 0) {
    return false;
  }

  // nghttp3 はストリームタイプをアサーションでチェックする
  // クライアント: client_bidi (%4==0) or server_bidi (%4==1) or client_uni
  // (%4==2)
  // サーバー: client_bidi (%4==0) or server_bidi (%4==1) or server_uni (%4==3)
  int mod = stream_id % 4;
  if (is_server_) {
    // サーバーは client_bidi, server_bidi, server_uni のみ許可
    if (mod != 0 && mod != 1 && mod != 3) {
      return false;
    }
  } else {
    // クライアントは client_bidi, server_bidi, client_uni のみ許可
    if (mod != 0 && mod != 1 && mod != 2) {
      return false;
    }
  }

  // nghttp3 に WebTransport データストリームを登録
  // nghttp3 が WT ヘッダ (ストリームタイプ + セッション ID) を出力する
  nghttp3_data_reader dr;
  dr.read_data = wt_data_read_callback;

  int rv = nghttp3_conn_open_wt_data_stream(conn_, session_id, stream_id, &dr,
                                            nullptr);
  if (rv != 0) {
    return false;
  }

  // ストリーム情報を記録
  StreamInfo info;
  info.stream_id = stream_id;
  info.session_id = session_id;
  info.is_unidirectional = is_unidirectional;
  info.is_incoming = false;
  info.is_write_registered = true;

  stream_info_[stream_id] = info;

  return true;
}

void H3Session::send_stream_data(int64_t stream_id,
                                 const std::vector<uint8_t>& data,
                                 bool fin) {
  if (!conn_) {
    return;
  }

  // stream_info_ に未登録のストリームへの送信はセッション ID を復元できない
  // ため黙って無視する。セッション ID 集合の先頭要素をフォールバックに使う
  // と、複数セッション時に誤ったセッションへデータが属し得るため使わない
  auto it = stream_info_.find(stream_id);
  if (it == stream_info_.end()) {
    return;
  }

  // 書き込み未登録のストリーム (受信済みのリモート起動ストリーム等) は
  // エントリのセッション ID で登録を試み、成功した場合のみ送信する
  if (!it->second.is_write_registered) {
    // 受信済みの単方向ストリーム (クライアント起点 %4==2 / サーバー起点
    // %4==3) は送信方向が一方向のみ (RFC 9000 Section 2.1) のため、書き込み
    // 登録すると nghttp3 の方向性の assert がデバッグビルドで発火して
    // abort し得る。受信済み単方向ストリームへの送信は黙って無視する
    // (未登録ストリームへの送信が無視されるのと同じ扱い。自側で
    // open_stream した単方向ストリームは is_write_registered が true のため
    // この分岐に入らない)
    if (it->second.is_unidirectional) {
      return;
    }
    nghttp3_data_reader dr;
    dr.read_data = wt_data_read_callback;

    int rv = nghttp3_conn_open_wt_data_stream(conn_, it->second.session_id,
                                              stream_id, &dr, nullptr);
    if (rv != 0) {
      return;
    }
    // open_wt_data_stream は同期コールバックを呼ばないため、登録成功後も
    // it は stream_info_ のエントリを指したまま有効である
    it->second.is_write_registered = true;
  }

  // ストリームバッファに追加
  StreamBuffer buf;
  buf.data = data;
  buf.fin = fin;
  stream_buffers_[stream_id].push_back(std::move(buf));

  // nghttp3 にデータが利用可能になったことを通知
  // これにより read_data コールバックが呼ばれるようになる
  nghttp3_conn_resume_stream(conn_, stream_id);
}

nghttp3_ssize H3Session::read_data_callback(int64_t stream_id,
                                            nghttp3_vec* vec,
                                            size_t veccnt,
                                            uint32_t* pflags) {
  if (veccnt == 0) {
    return 0;
  }

  auto it = stream_buffers_.find(stream_id);
  if (it == stream_buffers_.end() || it->second.empty()) {
    return NGHTTP3_ERR_WOULDBLOCK;
  }

  auto& buffers = it->second;

  // 送信済み (offset 済み) で FIN なしのバッファはスキップして次へ進む
  // (ここで pop_front すると ALIEN 参照中のバッファが free され、
  //  ダングリングポインタになるため、削除は acked_stream_data_cb に任せる)
  for (auto itb = buffers.begin(); itb != buffers.end(); ++itb) {
    auto& front = *itb;
    size_t remaining = front.data.size() - front.offset;
    if (remaining == 0) {
      if (front.fin) {
        *pflags |= NGHTTP3_DATA_FLAG_EOF;
        // 読み出し済みの空エントリを削除する
        // データ量 0 のため acked_stream_data コールバックは発火せず、
        // ACK 経路では解放されない
        // ここでは vec を返していないため pop_front しても安全
        buffers.pop_front();
        if (buffers.empty()) {
          stream_buffers_.erase(it);
        }
        return 0;
      }
      continue;
    }

    vec[0].base = const_cast<uint8_t*>(front.data.data() + front.offset);
    vec[0].len = remaining;
    front.offset = front.data.size();

    // 末尾バッファかつ FIN なら EOF を付ける
    if (front.fin && std::next(itb) == buffers.end()) {
      *pflags |= NGHTTP3_DATA_FLAG_EOF;
    }

    return 1;
  }

  return NGHTTP3_ERR_WOULDBLOCK;
}

void H3Session::send_datagram(int64_t session_id,
                              const std::vector<uint8_t>& data) {
  // セッション終了を学習したエンドポイントは新しいデータグラムを送信しては
  // ならない (draft-ietf-webtrans-http3-16 Section 6 の MUST 「it MUST NOT
  // send any new datagrams or open any new streams」)。session_ids_ から
  // セッション ID が削除される経路 (close_stream による CONNECT ストリーム
  // のクローズ / close_session / recv_wt_close_session_cb / end_headers_cb
  // での非 2xx 応答受信 / サーバー側の reject_session による非 2xx 拒否) は
  // すべて session_ids_ のメンバーシップ確認に
  // 依存するため、メンバーシップ確認で session_ids_ に含まれないセッション
  // ID への送信を黙って無視する (open_stream は失敗を false で返すのに対し、
  // 本対応は void のまま黙って無視する。機構も異なる: open_stream は
  // nghttp3 のエラー返却に依存し、本対応は session_ids_ の直接確認)。
  // 受理前 FIN を検知したセッション (終了を学習済みだが close_stream による
  // 後始末前) も同様に無視する。楽観的送信 (draft-ietf-webtrans-http3-16
  // Section 4) は妨げない: クライアントは connect 直後に、サーバーは
  // CONNECT リクエスト受信時 (end_headers_cb) に session_ids_ へ挿入される
  if (session_ids_.count(session_id) == 0 ||
      pending_pre_accept_fin_session_ids_.count(session_id) > 0 ||
      pre_accept_fin_accepted_session_ids_.count(session_id) > 0) {
    return;
  }

  // Quarter Stream ID = Session ID / 4 を nghttp3 の varint でエンコードする
  uint64_t quarter_stream_id = static_cast<uint64_t>(session_id) / 4;
  size_t varint_len = nghttp3_put_uvarintlen(quarter_stream_id);

  std::vector<uint8_t> datagram(varint_len + data.size());
  nghttp3_put_uvarint(datagram.data(), quarter_stream_id);
  std::copy(data.begin(), data.end(),
            datagram.begin() + static_cast<std::ptrdiff_t>(varint_len));

  pending_datagrams_.push_back(std::move(datagram));
}

int64_t H3Session::close_stream(int64_t stream_id, uint64_t error_code) {
  if (!conn_) {
    return -1;
  }

  // QPACK デコードブロック中 fin を検知した保留記録を除去する。ブロック中
  // にリセットされたストリームは end_headers_cb が発火せず、移行条件
  // (session_ids_ への挿入) も成立しないため、そのままでは記録が接続終了
  // まで残留する。ここで除去する (移行済みのストリームは既に erase 済み
  // のため no-op)
  pending_qpack_blocked_fin_stream_ids_.erase(stream_id);

  // セッション ID の復元とバッファ削除は nghttp3 呼び出しより前に行う
  // (nghttp3_conn_close_stream は同期実行される stream_close_cb を呼び、
  // stream_close_cb が stream_info_ からエントリを削除するため)
  int64_t session_id = -1;
  auto it = stream_info_.find(stream_id);
  if (it != stream_info_.end()) {
    session_id = it->second.session_id;
  }
  // CONNECT ストリームは stream_info_ に登録されないため session_ids_ で判定する。
  // セッション ID は CONNECT ストリーム ID そのもの (draft-ietf-webtrans-http3-16
  // Section 2.2)。CONNECT ストリームのリセットはセッション終了の正当な経路
  // (Section 6 のセッション終了条件の 1 つ目)。QUIC のストリーム ID は接続内で
  // 一意なため、session_ids_ に含まれる ID を持つ未登録ストリームは CONNECT
  // ストリームしかあり得ない
  bool is_connect_stream =
      (it == stream_info_.end() && session_ids_.count(stream_id) > 0);
  if (is_connect_stream) {
    session_id = stream_id;
    // セッションに属するデータストリームの送信バッファを stream_info_ の
    // 走査で削除する。nghttp3_conn_close_stream の同期コールバックで
    // stream_close_cb がエントリごと消すケースに備えた安全網であり、
    // エントリが残ったままなら後段の erase_session_streams が同じバッファ
    // を削除する (実質重複だが無害)。stream_info_ エントリ自体の清掃は
    // nghttp3_conn_close_stream の同期コールバック (reset_stream_cb /
    // stop_sending_cb / stream_close_cb) の後に erase_session_streams で
    // 行う (先に削除すると同期コールバック内のセッション ID 復元が
    // できなくなる)
    for (const auto& pair : stream_info_) {
      if (pair.second.session_id == session_id) {
        stream_buffers_.erase(pair.first);
      }
    }
  }
  // 該当ストリームの送信バッファを削除する (CONNECT ストリームは stream_info_
  // 走査の対象外のため自身のエントリをここで削除する)。リセット後は再送信が
  // 停止するため未送信データを保持する義務がない (RFC 9000 Section 19.4)
  stream_buffers_.erase(stream_id);

  // WT ヘッダー未受信 (stream_info_ に未登録) のストリーム等は
  // NGHTTP3_ERR_STREAM_NOT_FOUND が返るため戻り値は無視し、
  // 復元したセッション ID (復元できない場合は -1) を返す
  (void)nghttp3_conn_close_stream(conn_, stream_id, error_code);
  stream_info_.erase(stream_id);

  if (is_connect_stream) {
    // セッション終了の後始末 (draft-ietf-webtrans-http3-16 Section 6 の
    // セッション終了条件の 1 つ目: CONNECT ストリームのクローズ。リセットと
    // FIN の両方の経路から呼ばれる)。セッションに属するデータストリームの
    // stream_info_ エントリを清掃し、session_ids_ から削除する。清掃は
    // nghttp3_conn_close_stream の同期コールバック (reset_stream_cb /
    // stop_sending_cb / stream_close_cb) の後に行い、コールバック内の
    // セッション ID 復元 (stream_info_ の残存に依存) を壊さない
    erase_session_streams(session_id);
    session_ids_.erase(session_id);

    // セッション終了イベントを発火する。error_message は空とする
    H3Event event;
    event.type = H3EventType::SessionClosed;
    event.session_id = session_id;
    event.error_code = error_code;
    push_event(std::move(event));
  }
  return session_id;
}

void H3Session::reset_stream(int64_t stream_id, uint64_t error_code) {
  // nghttp3 への通知は close_stream と同じ。QUIC RESET_STREAM は高レベル側で送る
  close_stream(stream_id, error_code);
}

void H3Session::erase_session_streams(int64_t session_id) {
  // 破棄されたストリームの未送信データを接続終了まで保持しない。
  // ローカル送信バッファの破棄は draft-ietf-webtrans-http3-16 Section 6 の
  // セッション終了時のストリーム破棄 (MUST) に反しない
  std::vector<int64_t> streams_to_remove;
  for (const auto& pair : stream_info_) {
    if (pair.second.session_id == session_id) {
      streams_to_remove.push_back(pair.first);
    }
  }
  for (int64_t stream_id : streams_to_remove) {
    stream_buffers_.erase(stream_id);
    stream_info_.erase(stream_id);
  }
}

void H3Session::discard_stale_2xx() {
  // 遅延クローズ保留中に WT_CLOSE_SESSION を受信したセッションの未送信
  // 2xx を破棄する。nghttp3 には 2xx のみをキャンセルする API が存在せず、
  // close_stream (nghttp3_conn_close_stream) がストリームの送信キュー全体
  // (未送信 2xx を含む) を破棄する唯一の手段である。
  // pre_accept_fin_accepted_session_ids_ からは先に除去する: 除去しないと、
  // 存在しないストリームは stream_flushed が 1 を返すため、次の
  // get_streams_to_send の遅延クローズループで 2 回目の close_stream が
  // 実行される。現在の依存 nghttp3 では存在しないストリームへの
  // close_stream は NGHTTP3_ERR_STREAM_NOT_FOUND を返して stream_close_cb
  // を発火しないが、nghttp3 の実装変更でイベント個数が変わり得るため、
  // 先に除去して防衛する。close_stream の副作用として stream_close_cb が
  // 発火して STREAM_CLOSED イベント (session_id = -1) が積まれる (既存の
  // 遅延クローズでも同様)。SessionClosed は発火しない (session_ids_ から
  // 削除済みのため)。全走査でよい: 他セッションの保留エントリも破棄対象
  // であり、全走査で破棄されるのが正しい
  for (int64_t session_id : pending_stale_2xx_discard_session_ids_) {
    pre_accept_fin_accepted_session_ids_.erase(session_id);
    close_stream(session_id, 0);
  }
  pending_stale_2xx_discard_session_ids_.clear();
}

void H3Session::handle_wt_close_session_error(int64_t session_id) {
  // WT_CLOSE_SESSION の Application Error Message の不正 (1024 バイト超・
  // 4 バイト未満の不正な長さ・不正 UTF-8。draft-16 Section 6 の MUST:
  // H3_MESSAGE_ERROR での CONNECT ストリームのリセット) のセッション終了処理。呼び出し元は
  // receive_stream_data の負値分岐 (read_stream2 からの復帰後) と
  // accept_session の確認失敗分岐 (アプリ呼び出し) の両方で、nghttp3 の
  // コールバック内からは呼ばれない (再入なし)。
  // リセットの送出手段: nghttp3 の公開 API に CONNECT ストリームの
  // reset_stream_cb を発火させる手段はない (close_stream / close_stream2 は
  // conn_delete_stream を呼ぶのみで、reset_stream_cb は内部の
  // nghttp3_conn_abort_stream のみが発火させる)。そのため QUIC RESET_STREAM
  // の送出は高レベル層の既存変換 (ResetStream イベント →
  // quic_connection.reset_stream) に委ね、ここでイベントを明示 push する。
  // nghttp3 側には close_stream (CONNECT ストリームの消去) で終了を伝える。
  // このとき nghttp3 内部 (conn_unlink_wt_session) が残留データストリームを
  // WT_SESSION_GONE (0x170D7B68) で破棄し (reset_stream_cb / stop_sending_cb
  // 発火)、イベント経由で QUIC 層へ通知される。close_stream はまた
  // SessionClosed イベント (セッション終了の通知) を積む
  if (session_ids_.count(session_id) == 0) {
    // 既に終了を学習済みのセッションは後始末済み (セッション終了の
    // 後始末と SessionClosed の通知は close_stream 側で行われた)。
    // 0x010E 自体の通知は「まだ行っていない」場合があるが、終了済み
    // セッションのリセット送出は意味を成さないため何もしない
    return;
  }
  close_stream(session_id, kH3MessageError);
  H3Event event;
  event.type = H3EventType::ResetStream;
  event.stream_id = session_id;
  event.session_id = session_id;
  event.error_code = kH3MessageError;
  push_event(std::move(event));
}

void H3Session::close_session(int64_t session_id,
                              uint64_t error_code,
                              const std::string& error_message) {
  if (!conn_) {
    return;
  }

  // nghttp3 の WebTransport セッション終了 API を使用
  // WT_CLOSE_SESSION カプセルを送信し、全ストリームを適切にシャットダウン
  // エラーメッセージはバイト単位で 1024 に切り詰めた後、末尾が不完全な
  // UTF-8 シーケンスになる場合は文字境界まで後退させる (draft-16 Section 6
  // の「Senders that truncate an application-supplied message MUST do so at a
  // UTF-8 character boundary」。is_valid_utf8 は切り詰めで生じる不完全な終端
  // を検出する。ASCII のみのメッセージでは従来どおり 1024 バイトで切る。
  // 1024 バイト超のメッセージをそのまま渡すと nghttp3 が
  // NGHTTP3_ERR_INVALID_ARGUMENT を返し、close_session は黙って失敗する)。
  // なお Python str は常に well-formed UTF-8 のため、先頭 1024 バイト内の
  // 不正シーケンスは存在せず、後退は不完全な末尾に対してのみ発生する
  std::string trimmed_message = error_message;
  if (trimmed_message.size() > kMaxApplicationErrorMessageBytes) {
    size_t message_len = kMaxApplicationErrorMessageBytes;
    while (message_len > 0 &&
           !is_valid_utf8(
               reinterpret_cast<const uint8_t*>(trimmed_message.data()),
               message_len)) {
      message_len -= 1;
    }
    trimmed_message.resize(message_len);
  }
  int rv = nghttp3_conn_close_wt_session(
      conn_, session_id, static_cast<uint32_t>(error_code),
      reinterpret_cast<const uint8_t*>(trimmed_message.data()),
      trimmed_message.size());

  if (rv != 0) {
    return;
  }

  // ローカルのストリーム情報と送信バッファをクリーンアップする
  erase_session_streams(session_id);

  session_ids_.erase(session_id);

  // セッション終了イベント
  H3Event event;
  event.type = H3EventType::SessionClosed;
  event.session_id = session_id;
  event.error_code = error_code;
  event.error_message = trimmed_message;
  push_event(std::move(event));
}

std::optional<H3Event> H3Session::next_event() {
  if (events_.empty()) {
    return std::nullopt;
  }

  H3Event event = std::move(events_.front());
  events_.pop_front();
  return event;
}

std::vector<std::pair<std::string, bool>> H3Session::get_required_streams()
    const {
  std::vector<std::pair<std::string, bool>> result;
  result.emplace_back("control", false);        // 単方向、送信
  result.emplace_back("qpack_encoder", false);  // 単方向、送信
  result.emplace_back("qpack_decoder", false);  // 単方向、送信
  return result;
}

bool H3Session::is_closed() const {
  return closed_;
}

std::vector<int64_t> H3Session::get_session_ids() const {
  return std::vector<int64_t>(session_ids_.begin(), session_ids_.end());
}

std::vector<StreamInfo> H3Session::get_session_streams(
    int64_t session_id) const {
  std::vector<StreamInfo> result;
  for (const auto& pair : stream_info_) {
    if (pair.second.session_id == session_id) {
      result.push_back(pair.second);
    }
  }
  return result;
}

void H3Session::set_max_client_streams_bidi(uint64_t max_streams) {
  if (!conn_) {
    return;
  }
  nghttp3_conn_set_max_client_streams_bidi(conn_, max_streams);
}

void H3Session::block_stream(int64_t stream_id) {
  if (!conn_) {
    return;
  }
  nghttp3_conn_block_stream(conn_, stream_id);
}

bool H3Session::unblock_stream(int64_t stream_id) {
  if (!conn_) {
    return false;
  }
  return nghttp3_conn_unblock_stream(conn_, stream_id) == 0;
}

void H3Session::max_concurrent_streams(size_t n) {
  if (!conn_) {
    return;
  }
  nghttp3_conn_set_max_concurrent_streams(conn_, n);
}

std::optional<bool> H3Session::has_stream_buffer(int64_t stream_id) const {
  if (stream_buffers_.find(stream_id) == stream_buffers_.end()) {
    return std::nullopt;
  }
  return true;
}

std::optional<bool> H3Session::has_pending_qpack_blocked_fin_stream(
    int64_t stream_id) const {
  if (pending_qpack_blocked_fin_stream_ids_.count(stream_id) == 0) {
    return std::nullopt;
  }
  return true;
}

std::optional<int> H3Session::stream_writable(int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return nghttp3_conn_is_stream_writable2(conn_, stream_id);
}

std::optional<int> H3Session::stream_flushed(int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  return nghttp3_conn_is_stream_flushed(conn_, stream_id);
}

std::optional<int64_t> H3Session::stream_wt_session_id(
    int64_t stream_id) const {
  if (!conn_ || closed_) {
    return std::nullopt;
  }
  int64_t session_id = nghttp3_conn_get_stream_wt_session_id(conn_, stream_id);
  if (session_id < 0) {
    return std::nullopt;
  }
  return session_id;
}

void H3Session::push_event(H3Event event) {
  events_.push_back(std::move(event));
}

// ========== nghttp3 コールバック実装 ==========

int H3Session::acked_stream_data_cb(nghttp3_conn* /*conn*/,
                                    int64_t stream_id,
                                    uint64_t datalen,
                                    void* conn_user_data,
                                    void* /*stream_user_data*/) {
  auto* session = static_cast<H3Session*>(conn_user_data);
  if (!session) {
    return 0;
  }

  // ACK されたデータを stream_buffers_ から削除
  auto it = session->stream_buffers_.find(stream_id);
  if (it == session->stream_buffers_.end()) {
    return 0;
  }

  auto& buffers = it->second;
  uint64_t remaining = datalen;

  while (remaining > 0 && !buffers.empty()) {
    auto& front = buffers.front();
    if (front.data.size() <= remaining) {
      remaining -= front.data.size();
      buffers.pop_front();
    } else {
      // 部分的に ACK された場合 (通常は発生しないが念のため)
      front.data.erase(front.data.begin(),
                       front.data.begin() + static_cast<ptrdiff_t>(remaining));
      remaining = 0;
    }
  }

  // 空になったエントリを削除する
  if (buffers.empty()) {
    session->stream_buffers_.erase(it);
  }

  return 0;
}

int H3Session::stream_close_cb(nghttp3_conn* conn,
                               int64_t stream_id,
                               uint64_t app_error_code,
                               void* conn_user_data,
                               void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  // ストリーム終了イベント
  H3Event event;
  event.type = H3EventType::StreamClosed;
  event.stream_id = stream_id;
  event.error_code = app_error_code;

  // セッション ID を取得
  auto it = session->stream_info_.find(stream_id);
  if (it != session->stream_info_.end()) {
    event.session_id = it->second.session_id;
    session->stream_info_.erase(it);
  }

  session->push_event(std::move(event));
  return 0;
}

int H3Session::recv_data_cb(nghttp3_conn* conn,
                            int64_t stream_id,
                            const uint8_t* data,
                            size_t datalen,
                            void* conn_user_data,
                            void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  H3Event event;
  event.type = H3EventType::StreamData;
  event.stream_id = stream_id;
  event.data.assign(data, data + datalen);

  // セッション ID を取得
  auto it = session->stream_info_.find(stream_id);
  if (it != session->stream_info_.end()) {
    event.session_id = it->second.session_id;
  }

  session->push_event(std::move(event));
  return 0;
}

int H3Session::deferred_consume_cb(nghttp3_conn* conn,
                                   int64_t stream_id,
                                   size_t consumed,
                                   void* conn_user_data,
                                   void* stream_user_data) {
  (void)conn;
  (void)stream_id;
  (void)consumed;
  (void)conn_user_data;
  (void)stream_user_data;
  return 0;
}

int H3Session::begin_headers_cb(nghttp3_conn* conn,
                                int64_t stream_id,
                                void* conn_user_data,
                                void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);
  session->pending_headers_[stream_id].clear();
  return 0;
}

int H3Session::recv_header_cb(nghttp3_conn* conn,
                              int64_t stream_id,
                              int32_t token,
                              nghttp3_rcbuf* name,
                              nghttp3_rcbuf* value,
                              uint8_t flags,
                              void* conn_user_data,
                              void* stream_user_data) {
  (void)conn;
  (void)flags;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  nghttp3_vec name_vec = nghttp3_rcbuf_get_buf(name);
  nghttp3_vec value_vec = nghttp3_rcbuf_get_buf(value);

  std::string header_name(reinterpret_cast<const char*>(name_vec.base),
                          name_vec.len);
  std::string header_value(reinterpret_cast<const char*>(value_vec.base),
                           value_vec.len);

  session->pending_headers_[stream_id].emplace_back(std::move(header_name),
                                                    std::move(header_value));
  return 0;
}

int H3Session::end_headers_cb(nghttp3_conn* conn,
                              int64_t stream_id,
                              int fin,
                              void* conn_user_data,
                              void* stream_user_data) {
  (void)conn;
  (void)fin;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  auto it = session->pending_headers_.find(stream_id);
  if (it == session->pending_headers_.end()) {
    return 0;
  }

  const auto& headers = it->second;

  // WebTransport CONNECT リクエストかチェック
  bool is_connect = false;
  bool is_webtransport = false;
  std::string status;

  for (const auto& header : headers) {
    if (header.first == ":method" && header.second == "CONNECT") {
      is_connect = true;
    }
    if (header.first == ":protocol") {
      if (header.second == "webtransport-h3" ||
          header.second == "webtransport") {
        // "webtransport-h3" はネイティブ HTTP/3 セッションのトークン
        // (draft-ietf-webtrans-http3-16 Section 3.2)。
        // "webtransport" は現行仕様ではカプセルベースプロトコル用トークン
        // (draft-16 Section 2.1.2) であるが、実ブラウザ (Chromium /
        // WebKit) および Shiguredo WebTransport DevTools は現時点でも
        // ":protocol: webtransport" で CONNECT する (実測済み)。
        // draft-16 は「unknown の :protocol を受信した場合は 501 を返す
        // SHOULD (RFC 9220 Section 3)」を定めるが、実ブラウザ互換を優先して
        // 本実装は "webtransport" もネイティブセッションとして受理する
        // (将来の仕様改訂でトークンが統一された場合に削除する)。
        // カプセルベースプロトコルのトークンとネイティブセッションの混同は
        // ストリーム先頭シグナルの意味が異なり得る (draft-16 Section 2.1.2)
        // ため、カプセルベースプロトコルを実装するまで本実装の判定は
        // "webtransport" を含む。これは仕様との既知の逸脱である
        is_webtransport = true;
      }
    }
    if (header.first == ":status") {
      status = header.second;
    }
  }

  if (is_connect && is_webtransport && session->is_server_) {
    // Origin 検証に失敗した場合は 403 で拒否する
    if (!session->verify_origin(headers)) {
      session->reject_session(stream_id, 403);
      // QPACK デコードブロック中に fin を検知した保留ストリームは、CONNECT
      // 判定されなかったため記録を除去する (receive_stream_data の移行処理
      // は session_ids_ への挿入を条件とするため、このストリームは移行され
      // ない)
      session->pending_qpack_blocked_fin_stream_ids_.erase(stream_id);
      session->pending_headers_.erase(it);
      return 0;
    }

    // サーバー: WebTransport セッションリクエストを受信
    session->session_ids_.insert(stream_id);

    H3Event event;
    event.type = H3EventType::SessionReady;
    event.session_id = stream_id;
    session->push_event(std::move(event));
  } else if (!session->is_server_ && !status.empty()) {
    // クライアント: WebTransport セッション応答の処理。
    // 2xx 応答の受信でセッションが確立される (draft-ietf-webtrans-http3-16
    // Section 3.2 の「クライアントの視点では、2xx 応答を受信したときに
    // セッションが確立される」)。nghttp3 は 2xx 全般をセッション確立として
    // 扱う (status_code / 100 == 2 による confirm。201 等の 2xx 非 200
    // 応答でもセッションが確定する) ため、誤って削除しない。
    // SESSION_READY は 200 のときのみ発火する (2xx 非 200 応答で
    // SESSION_READY が発火しないのは既存の制約として残す)
    if (status[0] != '2') {
      // 非 2xx 応答 (拒否・リダイレクト) ではセッションは確立されなかった
      // (draft-ietf-webtrans-http3-16 Section 3.2 では 2xx のみが確立)。
      // 楽観的送信 (Section 4) は応答受信までの許容であり、拒否後の送信は
      // 塞がなければならない。nghttp3 は非 2xx 応答を受信した CONNECT
      // ストリームを reset する (abort_stream。status_code / 100 == 2 が
      // 成立しないため) ため end_stream コールバックが発火せず、既存の
      // FIN 経路では session_ids_ から削除されない。ここで session_ids_
      // から削除して、拒否されたセッション ID 宛の send_datagram /
      // open_stream を塞ぐ。1xx 中間応答もこの分岐で削除する: 1xx は
      // 確立応答ではなく (Section 3.2)、現在の依存 nghttp3 は 1xx 受信時
      // に status_code を -1 へ戻して非 2xx として abort する (nghttp3
      // が 1xx を中間応答として扱う更新が入った場合はこの削除の見直しが
      // 必要)。SessionClosed は発火しない (一度も確立されていない
      // セッションの終了通知という意味論が合わないため、黙って削除する)。
      // 削除後は close_stream の CONNECT ストリーム判定 (session_ids_ の
      // メンバーシップ確認) が成立しなくなり、二重発火の経路も残らない
      session->session_ids_.erase(stream_id);
    } else if (status == "200" && session->session_ids_.count(stream_id) > 0) {
      H3Event event;
      event.type = H3EventType::SessionReady;
      event.session_id = stream_id;
      session->push_event(std::move(event));
    }
  }

  // CONNECT 判定されなかったサーバー側ストリーム (通常の HTTP リクエスト等)
  // の QPACK ブロック中 fin 記録を除去する。CONNECT 判定されたストリームの
  // 記録は receive_stream_data の後段で pending_pre_accept_fin_session_ids_
  // へ移行されるため、ここでは除去しない
  if (session->is_server_ && !(is_connect && is_webtransport)) {
    session->pending_qpack_blocked_fin_stream_ids_.erase(stream_id);
  }

  session->pending_headers_.erase(it);
  return 0;
}

int H3Session::end_stream_cb(nghttp3_conn* conn,
                             int64_t stream_id,
                             void* conn_user_data,
                             void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  // 受信側のストリームが FIN で閉じられたときに呼ばれる。CONNECT ストリーム
  // (クライアント起動双方向ストリーム) の FIN はセッション終了の正当な経路
  // であり (draft-ietf-webtrans-http3-16 Section 6 のセッション終了条件の
  // 1 つ目)、セッション終了の検知が必要。CONNECT ストリームの判定は
  // session_ids_ のメンバーシップで行う (データストリームは stream_info_ に
  // 登録されるだけで session_ids_ には含まれないため誤検知しない)。
  // このコールバックは nghttp3_conn_read_stream2 の処理中に同期発火するため、
  // ここで nghttp3 を再度呼ぶと再入による状態破壊の恐れがある。セッション ID
  // を保留集合に記録するだけに留め、後始末は receive_stream_data が
  // nghttp3_conn_read_stream2 から戻った後に close_stream で行う
  if (session->session_ids_.count(stream_id) > 0) {
    session->pending_fin_session_ids_.insert(stream_id);
  }
  return 0;
}

int H3Session::stop_sending_cb(nghttp3_conn* conn,
                               int64_t stream_id,
                               uint64_t app_error_code,
                               void* conn_user_data,
                               void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  // nghttp3 が QUIC STOP_SENDING の送出を要求している
  auto* session = static_cast<H3Session*>(conn_user_data);

  H3Event event;
  event.type = H3EventType::StopSending;
  event.stream_id = stream_id;
  event.error_code = app_error_code;

  auto it = session->stream_info_.find(stream_id);
  if (it != session->stream_info_.end()) {
    event.session_id = it->second.session_id;
  }

  session->push_event(std::move(event));
  return 0;
}

int H3Session::reset_stream_cb(nghttp3_conn* conn,
                               int64_t stream_id,
                               uint64_t app_error_code,
                               void* conn_user_data,
                               void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  // nghttp3 が QUIC RESET_STREAM の送出を要求している
  auto* session = static_cast<H3Session*>(conn_user_data);

  H3Event event;
  event.type = H3EventType::ResetStream;
  event.stream_id = stream_id;
  event.error_code = app_error_code;

  auto it = session->stream_info_.find(stream_id);
  if (it != session->stream_info_.end()) {
    event.session_id = it->second.session_id;
  }

  session->push_event(std::move(event));
  return 0;
}

int H3Session::shutdown_cb(nghttp3_conn* conn,
                           int64_t id,
                           void* conn_user_data) {
  (void)conn;
  (void)id;

  auto* session = static_cast<H3Session*>(conn_user_data);
  session->closed_ = true;
  return 0;
}

int H3Session::recv_settings2_cb(nghttp3_conn* conn,
                                 const nghttp3_proto_settings* settings,
                                 void* conn_user_data) {
  (void)conn;
  (void)settings;
  (void)conn_user_data;
  return 0;
}

int H3Session::recv_wt_data_cb(nghttp3_conn* conn,
                               int64_t session_id,
                               int64_t stream_id,
                               const uint8_t* data,
                               size_t datalen,
                               void* conn_user_data,
                               void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  // 終了したセッション ID 宛のデータストリーム (ghost ストリーム) は
  // アプリに配信しない。close_session (WT_CLOSE_SESSION 送出) と
  // recv_wt_close_session_cb (WT_CLOSE_SESSION 受信) は session_ids_ から
  // セッション ID を削除するが、nghttp3 の CONNECT ストリームはストリーム
  // テーブルに残存するため、セッション終了後にピアが開いたデータストリーム
  // は nghttp3 が受容してこのコールバックが呼ばれる。受理前 FIN を検知した
  // セッション (終了を学習済みだが close_stream による後始末前) も同様に
  // 配信しない。ここでセッションの終了状態 (session_ids_ に含まれない・
  // 受理前 FIN 検知済み) を確認し、終了したセッションの場合は StreamData
  // イベントを発火せず、stream_info_ への登録もスキップして破棄する。
  // 受理前 (accept_session 前) のデータは nghttp3 が WT_SESSION_BLOCKED
  // でバッファリングするため recv_wt_data_cb は呼ばれず、pending 集合の
  // チェックは防御的であり、実効的な判定は受理後 (accepted 集合) で
  // 行われる。根拠は draft-ietf-webtrans-http3-16 Section 6 の MUST
  // (終了を学習したエンドポイントは属するストリームの受信側の読み取りを
  // 中止する) と Section 4 の「closed session 宛のデータの扱いは Section 6
  // に従う」。本修正はアプリ配信の抑止のみであり、トランスポート側の
  // 読み取り中止 (STOP_SENDING / RESET_STREAM 送出) は実装しない
  // (スコープ外)。コールバック内で nghttp3 を再呼び出ししない (再入防止)
  // ため、トランスポート側の後始末 (close_stream) は行わない。破棄した
  // ghost ストリームはピアの FIN / RESET まで nghttp3 のストリームテーブル
  // に残存する (既知の制約)
  if (session->session_ids_.count(session_id) == 0 ||
      session->pending_pre_accept_fin_session_ids_.count(session_id) > 0 ||
      session->pre_accept_fin_accepted_session_ids_.count(session_id) > 0) {
    return 0;
  }

  // 受信したストリームを stream_info_ に登録 (まだ登録されていない場合)
  // 書き込み登録はまだなので is_write_registered = false
  if (session->stream_info_.find(stream_id) == session->stream_info_.end()) {
    StreamInfo info;
    info.stream_id = stream_id;
    info.session_id = session_id;
    info.is_unidirectional = (stream_id & 0x2) != 0;
    info.is_incoming = true;
    info.is_write_registered = false;
    session->stream_info_[stream_id] = info;
  }

  H3Event event;
  event.type = H3EventType::StreamData;
  event.session_id = session_id;
  event.stream_id = stream_id;
  event.data = std::vector<uint8_t>(data, data + datalen);
  session->push_event(std::move(event));

  return 0;
}

int H3Session::recv_wt_close_session_cb(nghttp3_conn* conn,
                                        int64_t session_id,
                                        uint32_t wt_error_code,
                                        const uint8_t* msg,
                                        size_t msglen,
                                        void* conn_user_data,
                                        void* stream_user_data) {
  (void)conn;
  (void)stream_user_data;

  auto* session = static_cast<H3Session*>(conn_user_data);

  // Application Error Message の不正検知 (draft-16 Section 6 の MUST:
  // 「If the Application Error Message exceeds 1024 bytes or is not valid
  // UTF-8, the receiver MUST reset the stream with code H3_MESSAGE_ERROR」)。
  // 1024 バイト超過は nghttp3 が LENGTH 段階で NGHTTP3_ERR_H3_MESSAGE_ERROR
  // を返すため、本コールバックが発火する前に弾かれる (ここに到達するのは
  // 不正 UTF-8 のみ。msglen > 1024 は nghttp3 の実装変更で到達し得る防御的
  // チェック)。検知した場合はコールバック内で nghttp3 を呼べない
  // (再入防止) ため、セッション ID の保留と非 0 の戻りだけを行う。
  // 非 0 を返すと nghttp3 は NGHTTP3_ERR_CALLBACK_FAILURE を返し、nghttp3 の
  // デフォルトのセッションシャットダウン (WT_SESSION_GONE = 0x170D7B68 での
  // リセット) を止める。0 を返すと 0x170D7B68 が先行し、リセットコードを
  // H3_MESSAGE_ERROR (0x010E) にできないため、この経路が仕様 MUST を満たす
  // 唯一の方法である。読み取りが CALLBACK_FAILURE で戻った先
  // (receive_stream_data の負値分岐 / accept_session の確認失敗分岐) で
  // handle_wt_close_session_error を実行する
  if (msglen > kMaxApplicationErrorMessageBytes ||
      (msglen > 0 && !is_valid_utf8(msg, msglen))) {
    session->pending_wt_close_session_error_session_id_ = session_id;
    return NGHTTP3_ERR_H3_MESSAGE_ERROR;
  }

  // 当該セッションに属するストリーム情報と送信バッファを削除する
  session->erase_session_streams(session_id);
  session->session_ids_.erase(session_id);

  // 遅延クローズ保留中 (受理済みで 2xx レスポンスが発生し得る) のセッション
  // は、未送信の 2xx を破棄するため close_stream を実行する必要がある。
  // recv_wt_close_session_cb は nghttp3_conn_read_stream2 の処理中に同期
  // 発火するため、コールバック内で nghttp3 を呼ぶと再入になる。セッション
  // ID を保留集合に記録し、receive_stream_data が nghttp3_conn_read_stream2
  // から戻った後に破棄する (詳細は discard_stale_2xx の実装コメント。
  // accept_session の confirm 処理中に発火した場合は accept_session 内で
  // 破棄する)。
  // 破棄記録の条件は次のいずれかとする:
  // - pre_accept_fin_accepted_session_ids_ に含まれる (遅延クローズ保留中。
  //   WT_CLOSE_SESSION 受信時に 2xx が未送信のため破棄対象になる)
  // - accept_session の confirm 処理中に発火した (accepting_session_id_ と
  //   一致する)。受理前にバッファされた WT_CLOSE_SESSION カプセルが confirm
  //   の処理中 (process_blocked_wt_stream_data) に同期処理される経路であり、
  //   2xx は submit 済みのため破棄対象になる。受理前 FIN がカプセルより先
  //   に届いていない (FIN 検知前に accept_session が実行される) 場合は移行
  //   処理が成立しないため、この条件が無いと破棄されない。confirm が失敗
  //   する場合は process_blocked_wt_stream_data が呼ばれず本コールバックも
  //   発火しない (万一発火済みで confirm が失敗した場合も、accept_session
  //   の失敗分岐で記録済みエントリが処理される。close_stream の CONNECT
  //   ストリーム判定は session_ids_ のメンバーシップに依存するため、
  //   SessionClosed は発火しない)
  // 未受理 (検知のみ。pending_pre_accept_fin_session_ids_ に含まれる) の
  // セッションは 2xx が未発生のため破棄対象がなく、対象外とする (同集合の
  // エントリは既存どおり残る)
  if (session->pre_accept_fin_accepted_session_ids_.count(session_id) > 0 ||
      session->accepting_session_id_ == session_id) {
    session->pending_stale_2xx_discard_session_ids_.insert(session_id);
  }

  H3Event event;
  event.type = H3EventType::SessionClosed;
  event.session_id = session_id;
  event.error_code = wt_error_code;
  if (msg != nullptr && msglen > 0) {
    event.error_message =
        std::string(reinterpret_cast<const char*>(msg), msglen);
  }
  session->push_event(std::move(event));

  return 0;
}

// ========== Python バインディング ==========

void bind_webtransport_h3(nb::module_& m) {
  auto h3_mod = m.def_submodule("h3", "WebTransport over HTTP/3");

  // H3SessionConfig
  nb::class_<H3SessionConfig>(h3_mod, "Config", "WebTransport over HTTP/3 設定")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_rw("max_field_section_size",
              &H3SessionConfig::max_field_section_size)
      .def_rw("qpack_max_dtable_capacity",
              &H3SessionConfig::qpack_max_dtable_capacity)
      .def_rw("qpack_blocked_streams", &H3SessionConfig::qpack_blocked_streams)
      .def_rw("is_server", &H3SessionConfig::is_server)
      .def_rw("allowed_origins", &H3SessionConfig::allowed_origins,
              "許可オリジンリスト (空なら全オリジンを受理)");

  // H3EventType
  nb::enum_<H3EventType>(h3_mod, "EventType", "WebTransport イベント種別")
      .value("SESSION_READY", H3EventType::SessionReady)
      .value("SESSION_CLOSED", H3EventType::SessionClosed)
      .value("STREAM_OPENED", H3EventType::StreamOpened)
      .value("STREAM_DATA", H3EventType::StreamData)
      .value("STREAM_CLOSED", H3EventType::StreamClosed)
      .value("RESET_STREAM", H3EventType::ResetStream)
      .value("STOP_SENDING", H3EventType::StopSending)
      .value("DATAGRAM", H3EventType::Datagram)
      .value("ERROR", H3EventType::Error);

  // H3Event
  nb::class_<H3Event>(h3_mod, "Event", "WebTransport イベント")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_ro("type", &H3Event::type)
      .def_ro("session_id", &H3Event::session_id)
      .def_ro("stream_id", &H3Event::stream_id)
      .def_prop_ro(
          "data",
          [](const H3Event& e) {
            return nb::bytes(reinterpret_cast<const char*>(e.data.data()),
                             e.data.size());
          },
          "イベントデータ")
      .def_ro("error_code", &H3Event::error_code)
      .def_ro("error_message", &H3Event::error_message)
      .def_ro("is_unidirectional", &H3Event::is_unidirectional);

  // StreamInfo
  nb::class_<StreamInfo>(h3_mod, "StreamInfo", "WebTransport ストリーム情報")
      .def(nb::init<>(), nb::sig("def __init__(self) -> None"))
      .def_ro("stream_id", &StreamInfo::stream_id)
      .def_ro("session_id", &StreamInfo::session_id)
      .def_ro("is_unidirectional", &StreamInfo::is_unidirectional)
      .def_ro("is_incoming", &StreamInfo::is_incoming)
      .def_ro("is_write_registered", &StreamInfo::is_write_registered);

  // H3Session
  nb::class_<H3Session>(h3_mod, "Session",
                        "WebTransport over HTTP/3 セッション")
      .def_static(
          "create_client",
          [](const H3SessionConfig& config) {
            auto session = H3Session::create_client(config);
            if (!session) {
              throw std::runtime_error(
                  "Failed to create WebTransport H3 client session");
            }
            return session.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_client(config: Config) -> Session"),
          "クライアントセッションを作成")
      .def_static(
          "create_server",
          [](const H3SessionConfig& config) {
            auto session = H3Session::create_server(config);
            if (!session) {
              throw std::runtime_error(
                  "Failed to create WebTransport H3 server session");
            }
            return session.release();
          },
          nb::arg("config"), nb::rv_policy::take_ownership,
          nb::sig("def create_server(config: Config) -> Session"),
          "サーバーセッションを作成")
      .def(
          "receive_stream_data",
          [](H3Session& s, int64_t stream_id, nb::bytes data, bool fin) {
            return s.receive_stream_data(
                stream_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                fin);
          },
          nb::arg("stream_id"), nb::arg("data"), nb::arg("fin") = false,
          nb::sig("def receive_stream_data(self, stream_id: int, data: "
                  "bytes, fin: bool = False) -> int"),
          "QUIC ストリームからデータを受信")
      .def(
          "receive_datagram",
          [](H3Session& s, nb::bytes data) {
            s.receive_datagram(
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::arg("data"),
          nb::sig("def receive_datagram(self, data: bytes) -> None"),
          "QUIC データグラムを受信")
      .def(
          "get_streams_to_send",
          [](H3Session& s) {
            auto streams = s.get_streams_to_send();
            std::vector<std::tuple<int64_t, nb::bytes, bool>> result;
            result.reserve(streams.size());
            for (auto& [stream_id, data, fin] : streams) {
              result.emplace_back(
                  stream_id,
                  nb::bytes(reinterpret_cast<const char*>(data.data()),
                            data.size()),
                  fin);
            }
            return result;
          },
          nb::sig("def get_streams_to_send(self) -> list[tuple[int, "
                  "bytes, bool]]"),
          "送信すべきストリームデータを取得")
      .def(
          "get_datagrams_to_send",
          [](H3Session& s) {
            auto datagrams = s.get_datagrams_to_send();
            std::vector<nb::bytes> result;
            result.reserve(datagrams.size());
            for (auto& data : datagrams) {
              result.emplace_back(nb::bytes(
                  reinterpret_cast<const char*>(data.data()), data.size()));
            }
            return result;
          },
          nb::sig("def get_datagrams_to_send(self) -> list[bytes]"),
          "送信すべきデータグラムを取得")
      .def("bind_control_stream", &H3Session::bind_control_stream,
           nb::arg("stream_id"),
           nb::sig("def bind_control_stream(self, stream_id: int) -> None"),
           "コントロールストリーム ID を設定")
      .def("bind_qpack_encoder_stream", &H3Session::bind_qpack_encoder_stream,
           nb::arg("stream_id"),
           nb::sig(
               "def bind_qpack_encoder_stream(self, stream_id: int) -> None"),
           "QPACK エンコーダーストリーム ID を設定")
      .def("bind_qpack_decoder_stream", &H3Session::bind_qpack_decoder_stream,
           nb::arg("stream_id"),
           nb::sig(
               "def bind_qpack_decoder_stream(self, stream_id: int) -> None"),
           "QPACK デコーダーストリーム ID を設定")
      .def("connect", &H3Session::connect, nb::arg("stream_id"), nb::arg("url"),
           nb::arg("origin") = "",
           nb::sig("def connect(self, stream_id: int, url: str, "
                   "origin: str = '') -> bool"),
           "WebTransport セッションを開始 (クライアント用)")
      .def("accept_session", &H3Session::accept_session, nb::arg("stream_id"),
           nb::sig("def accept_session(self, stream_id: int) -> bool"),
           "WebTransport セッションを受理 (サーバー用)")
      .def("reject_session", &H3Session::reject_session, nb::arg("stream_id"),
           nb::arg("status_code"),
           nb::sig("def reject_session(self, stream_id: int, status_code: int) "
                   "-> None"),
           "WebTransport セッションを拒否 (サーバー用)")
      .def("open_stream", &H3Session::open_stream, nb::arg("session_id"),
           nb::arg("stream_id"), nb::arg("is_unidirectional"),
           nb::sig("def open_stream(self, session_id: int, stream_id: int, "
                   "is_unidirectional: bool) -> bool"),
           "WebTransport ストリームを開く")
      .def(
          "send_stream_data",
          [](H3Session& s, int64_t stream_id, nb::bytes data, bool fin) {
            s.send_stream_data(
                stream_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()),
                fin);
          },
          nb::arg("stream_id"), nb::arg("data"), nb::arg("fin") = false,
          nb::sig("def send_stream_data(self, stream_id: int, data: "
                  "bytes, fin: bool = False) -> None"),
          "WebTransport ストリームにデータを送信")
      .def(
          "send_datagram",
          [](H3Session& s, int64_t session_id, nb::bytes data) {
            s.send_datagram(
                session_id,
                std::vector<uint8_t>(data.c_str(), data.c_str() + data.size()));
          },
          nb::arg("session_id"), nb::arg("data"),
          nb::sig("def send_datagram(self, session_id: int, data: bytes) "
                  "-> None"),
          "WebTransport データグラムを送信")
      .def("close_stream", &H3Session::close_stream, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def close_stream(self, stream_id: int, error_code: int = "
                   "0) -> int"),
           "WebTransport ストリームを閉じる (nghttp3 に通知)。戻り値は"
           "リセットされたストリームが属するセッション ID。復元できない"
           "場合は -1")
      .def("reset_stream", &H3Session::reset_stream, nb::arg("stream_id"),
           nb::arg("error_code") = 0,
           nb::sig("def reset_stream(self, stream_id: int, error_code: int = "
                   "0) -> None"),
           "WebTransport ストリームをリセットする (nghttp3 に通知)")
      .def("close_session", &H3Session::close_session, nb::arg("session_id"),
           nb::arg("error_code") = 0, nb::arg("error_message") = "",
           nb::sig("def close_session(self, session_id: int, error_code: int = "
                   "0, error_message: str = '') -> None"),
           "WebTransport セッションを閉じる")
      .def("next_event", &H3Session::next_event,
           nb::sig("def next_event(self) -> Event | None"),
           "次のイベントを取得")
      .def("get_required_streams", &H3Session::get_required_streams,
           nb::sig("def get_required_streams(self) -> list[tuple[str, bool]]"),
           "必要な QUIC ストリーム ID のリストを取得")
      .def("is_closed", &H3Session::is_closed,
           nb::sig("def is_closed(self) -> bool"), "接続が閉じられたか")
      .def("get_session_ids", &H3Session::get_session_ids,
           nb::sig("def get_session_ids(self) -> list[int]"),
           "確立されたセッション ID のリストを取得")
      .def("get_session_streams", &H3Session::get_session_streams,
           nb::arg("session_id"),
           nb::sig("def get_session_streams(self, session_id: int) -> "
                   "list[StreamInfo]"),
           "セッションに属するストリームを取得")
      .def("set_max_client_streams_bidi",
           &H3Session::set_max_client_streams_bidi, nb::arg("max_streams"),
           nb::sig("def set_max_client_streams_bidi(self, max_streams: int) -> "
                   "None"),
           "クライアントからの双方向ストリームの最大数を設定")
      .def("_has_stream_buffer", &H3Session::has_stream_buffer,
           nb::arg("stream_id"),
           nb::sig("def _has_stream_buffer(self, stream_id: int) -> "
                   "bool | None"),
           "テスト専用: ストリームの送信バッファエントリの有無を確認")
      .def("_has_pending_qpack_blocked_fin_stream",
           &H3Session::has_pending_qpack_blocked_fin_stream,
           nb::arg("stream_id"),
           nb::sig("def _has_pending_qpack_blocked_fin_stream(self, "
                   "stream_id: int) -> bool | None"),
           "テスト専用: QPACK ブロック中 fin の保留記録の有無を確認")
      .def("stream_writable", &H3Session::stream_writable, nb::arg("stream_id"),
           nb::sig("def stream_writable(self, stream_id: int) -> int | None"),
           "ストリームが書き込み可能か確認")
      .def("stream_flushed", &H3Session::stream_flushed, nb::arg("stream_id"),
           nb::sig("def stream_flushed(self, stream_id: int) -> int | None"),
           "ストリームの全送信データが QUIC スタックに受け渡し済みか確認")
      .def("stream_wt_session_id", &H3Session::stream_wt_session_id,
           nb::arg("stream_id"),
           nb::sig("def stream_wt_session_id(self, stream_id: int) -> "
                   "int | None"),
           "ストリームが属する WebTransport セッション ID を取得")
      .def("block_stream", &H3Session::block_stream, nb::arg("stream_id"),
           nb::sig("def block_stream(self, stream_id: int) -> None"),
           "ストリームの QUIC フロー制御ブロックを通知")
      .def("unblock_stream", &H3Session::unblock_stream, nb::arg("stream_id"),
           nb::sig("def unblock_stream(self, stream_id: int) -> bool"),
           "ストリームの QUIC フロー制御ブロック解除を通知")
      .def("max_concurrent_streams", &H3Session::max_concurrent_streams,
           nb::arg("n"),
           nb::sig("def max_concurrent_streams(self, n: int) -> None"),
           "同時ストリーム数のヒントを設定");
}

}  // namespace h3
}  // namespace webtransport
