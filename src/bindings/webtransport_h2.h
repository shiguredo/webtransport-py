/**
 * WebTransport over HTTP/2 バインディング (draft-ietf-webtrans-http2-15)
 *
 * Sans-IO スタイルの WebTransport over HTTP/2 実装
 * Capsule Protocol (RFC 9297) を使用
 */

#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/stl/function.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <nghttp2/nghttp2.h>

#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace nb = nanobind;

namespace webtransport {
namespace h2 {

/**
 * Capsule Type (draft-ietf-webtrans-http2-15 Section 6)
 *
 * WT_STREAM の Type は 0x190B4D3B..0x190B4D3C で、最下位ビットが FIN
 * (Section 6.4)。非終端は 0x190B4D3C、終端は 0x190B4D3B。
 */
enum class CapsuleType : uint64_t {
  // DATAGRAM (RFC 9297 Section 3.5 / draft Section 6.11)
  Datagram = 0x00,

  // WebTransport Capsules (draft-ietf-webtrans-http2-15)
  Padding = 0x190B4D38,
  WtResetStream = 0x190B4D39,
  WtStopSending = 0x190B4D3A,
  // FIN ビット付き終端 (LSB=1)
  WtStreamFin = 0x190B4D3B,
  // FIN なし (LSB=0)
  WtStream = 0x190B4D3C,
  WtMaxData = 0x190B4D3D,
  WtMaxStreamData = 0x190B4D3E,
  WtMaxStreamsBidi = 0x190B4D3F,
  WtMaxStreamsUni = 0x190B4D40,
  WtDataBlocked = 0x190B4D41,
  WtStreamDataBlocked = 0x190B4D42,
  WtStreamsBlockedBidi = 0x190B4D43,
  WtStreamsBlockedUni = 0x190B4D44,

  // Session Capsules (draft-ietf-webtrans-http3 参照)
  WtCloseSession = 0x2843,
  WtDrainSession = 0x78ae,
};

/**
 * WebTransport over HTTP/2 用 SETTINGS (draft-15 Section 11.2)
 * 将来コードポイントが変わる可能性がある。
 */
constexpr uint16_t SETTINGS_WT_ENABLED = 0x2b60;
constexpr uint16_t SETTINGS_WT_INITIAL_MAX_DATA = 0x2b61;
constexpr uint16_t SETTINGS_WT_INITIAL_MAX_STREAM_DATA_UNI = 0x2b62;
constexpr uint16_t SETTINGS_WT_INITIAL_MAX_STREAM_DATA_BIDI_LOCAL = 0x2b63;
constexpr uint16_t SETTINGS_WT_INITIAL_MAX_STREAMS_UNI = 0x2b64;
constexpr uint16_t SETTINGS_WT_INITIAL_MAX_STREAMS_BIDI = 0x2b65;
constexpr uint16_t SETTINGS_WT_INITIAL_MAX_STREAM_DATA_BIDI_REMOTE = 0x2b66;

/**
 * WebTransport over HTTP/2 設定
 */
struct H2SessionConfig {
  // 初期ウィンドウサイズ (HTTP/2)
  uint32_t initial_window_size = 65535;

  // 最大同時ストリーム数 (HTTP/2)
  uint32_t max_concurrent_streams = 100;

  // 最大フレームサイズ (HTTP/2)
  uint32_t max_frame_size = 16384;

  // 最大ヘッダーリストサイズ (HTTP/2)
  uint32_t max_header_list_size = 65536;

  // サーバーモードかどうか
  bool is_server = false;

  // WebTransport 初期フロー制御 (セッションレベル)。2^32 以上は
  // create_client / create_server が ValueError にする (SETTINGS は uint32)
  uint64_t wt_initial_max_data = 1048576;

  // WebTransport 初期フロー制御 (ストリームレベル)。2^32 以上は
  // create_client / create_server が ValueError にする (SETTINGS は uint32)
  uint64_t wt_initial_max_stream_data = 262144;

  // WebTransport 初期ストリーム数制限 (双方向)。2^32 以上は
  // create_client / create_server が ValueError にする (SETTINGS は uint32)
  uint64_t wt_initial_max_streams_bidi = 100;

  // WebTransport 初期ストリーム数制限 (単方向)。2^32 以上は
  // create_client / create_server が ValueError にする (SETTINGS は uint32)
  uint64_t wt_initial_max_streams_uni = 100;

  // 受理前楽観的カプセルの蓄積上限 (バイト) 。サーバーが受理前に届いた
  // カプセルを capsule_buffer に保持する上限であり、超過時は非 2xx (413)
  // で拒否する。既定 65536 は max_datagram_frame_size 既定と整合する
  // 楽観的データグラム数発分の目安である
  uint64_t wt_pre_accept_buffer_limit = 65536;

  // 単一カプセルのペイロード上限 (バイト) 。Length 解釈直後に検査し、
  // 超過時は WT_ERROR でセッションを閉じる。既定 1 MiB。自実装の送信は
  // 呼び出しごとに 1 カプセル化するため、1 MiB 超の単回送信はアプリ側で
  // 分割する前提とする
  uint64_t wt_max_capsule_payload_size = 1048576;

  // 許可オリジンリスト (サーバーのみ) 。空 (未設定) なら Origin 検証を
  // 行わず全オリジンを受理する。h3.Session の allowed_origins と対称
  std::vector<std::string> allowed_origins;
};

/**
 * WebTransport over HTTP/2 イベント種別
 */
enum class H2EventType {
  // セッション関連
  SessionReady,
  SessionClosed,
  SessionDraining,

  // ストリーム関連
  StreamData,
  StreamReset,
  StopSending,

  // データグラム
  Datagram,

  // エラー
  Error,

  // セッション拒否 (非 2xx 応答の受信。末尾に追加し既存バリアントの
  // 数値を変えない)
  SessionRejected,

  // GOAWAY 受信 (graceful shutdown の通知。末尾に追加し既存バリアントの
  // 数値を変えない)
  GoAway,
};

/**
 * WebTransport over HTTP/2 イベント
 */
struct H2Event {
  H2EventType type;
  int32_t session_id = -1;
  uint64_t stream_id = 0;
  std::vector<uint8_t> data;
  uint32_t error_code = 0;
  std::string error_message;
  bool fin = false;
  // SessionRejected 発火時にのみ意味を持つ HTTP status code。他イベントでは 0
  uint16_t status_code = 0;
  // SessionReady 発火時にのみ意味を持つ受信 HTTP ヘッダー (受信順の
  // name / value)。他イベントでは空
  std::vector<std::pair<std::string, std::string>> headers;
  // GoAway 発火時にのみ意味を持つ GOAWAY フレームの last_stream_id。
  // 他イベントでは 0
  int32_t last_stream_id = 0;
};

/**
 * WebTransport ストリーム状態
 */
enum class StreamState {
  // 送信側状態 (Ready → DataSent (FIN 送出) / ResetSent (リセット送出) の
  // 2 終端のみ。受信側の追跡は recv_state が担当する)
  Ready,
  DataSent,
  ResetSent,

  // 受信側状態
  Recv,
  DataRecvd,
  ResetRecvd,
};

/**
 * WebTransport ストリーム情報
 */
struct WtStreamInfo {
  uint64_t stream_id;
  bool is_local;
  bool is_unidirectional;

  // 送信側状態
  StreamState send_state = StreamState::Ready;
  uint64_t bytes_sent = 0;
  uint64_t max_stream_data_local = 0;

  // 受信側状態
  StreamState recv_state = StreamState::Recv;
  uint64_t bytes_received = 0;
  uint64_t max_stream_data_remote = 0;

  // 送信保留キュー (フロー制御で塞がれた送信の待機列)。超過送信は
  // セッションを閉じず、BLOCKED 送出後にここへ積み、対向の MAX 受信で
  // 送出を再開する。既存の送信バッファとは別にストリーム単位で順序を
  // 保つ。新規の無制限バッファに見えるが、フロー制御の窓で排出される
  // 一時待機列であり、受信側の無制限蓄積とは性質が異なる
  struct PendingSend {
    std::vector<uint8_t> data;
    bool fin = false;
  };
  std::deque<PendingSend> pending_sends;
  // 同一制限値での WT_STREAM_DATA_BLOCKED の重複送出を抑止する印
  bool stream_data_blocked_sent = false;
  // WT_STOP_SENDING を受信済みか (二重受信検出用)。セッション集合の
  // 安全弁上限に達しても実在ストリームの検出を維持する
  bool stop_sending_received = false;
};

/**
 * WebTransport セッション情報
 */
struct WtSessionInfo {
  int32_t http2_stream_id;
  bool is_established = false;

  // セッション終了を学習したか (ローカル close_session / サーバー側の
  // reject_session の 2xx 送出。WT_CLOSE_SESSION 受信はエントリ削除で表現する)。
  // is_established は connect 直後 (2xx 応答前) も false のため、楽観的送信
  // (draft-15 Section 3.2) を塞がないよう終了状態は専用フラグで管理する
  bool is_terminated = false;

  // フロー制御 (送信側)
  uint64_t bytes_sent = 0;
  uint64_t max_data_local = 0;
  // 対向から受信した直近の Maximum Data (SETTINGS 非 0 または
  // WT_MAX_DATA)。未受信は nullopt で、減少値判定の基準にならない
  // (draft-15 Section 6.5。自側 config へのフォールバックは廃止済み)
  std::optional<uint64_t> received_max_data;

  // フロー制御 (受信側)
  uint64_t bytes_received = 0;
  uint64_t max_data_remote = 0;

  // ストリーム数制限
  uint64_t next_bidi_stream_id = 0;
  uint64_t next_uni_stream_id = 0;
  uint64_t max_streams_bidi_local = 0;
  uint64_t max_streams_uni_local = 0;
  // 対向から受信した直近の Maximum Streams (SETTINGS 非 0 または
  // WT_MAX_STREAMS)。未受信は nullopt (draft-15 Section 6.7)
  std::optional<uint64_t> received_max_streams_bidi;
  std::optional<uint64_t> received_max_streams_uni;
  // 自側が SETTINGS / WT_MAX_STREAMS で広告した受信上限
  // (draft-15 Section 6.7。対向が開いてよい累積本数)
  uint64_t max_streams_bidi_remote = 0;
  uint64_t max_streams_uni_remote = 0;
  uint64_t streams_bidi_opened = 0;
  uint64_t streams_uni_opened = 0;

  // ストリーム管理
  std::map<uint64_t, WtStreamInfo> streams;

  // Capsule バッファ (受信中)
  std::vector<uint8_t> capsule_buffer;

  // 対向の初期ストリームデータ上限 (送信側クレジット)
  // draft-15 Section 4.3.1 / 4.3.2
  uint64_t peer_max_stream_data_uni = 0;
  // 自側が開いた双方向ストリーム向け (対向の BIDI_REMOTE)
  uint64_t peer_max_stream_data_bidi_local = 0;
  // 対向が開いた双方向ストリーム向け (対向の BIDI_LOCAL)
  uint64_t peer_max_stream_data_bidi_remote = 0;
  // ストリーム種別ごとの初期 Maximum Stream Data 受信値
  // (SETTINGS 非 0 / WebTransport-Init)
  std::optional<uint64_t> received_initial_max_stream_data_uni;
  std::optional<uint64_t> received_initial_max_stream_data_bidi_local;
  std::optional<uint64_t> received_initial_max_stream_data_bidi_remote;
  // ストリーム未作成時も含め、 WT_MAX_STREAM_DATA で受信した直近値
  // (draft-15 Section 6.6 の「previously received value」)。
  // セッション破棄まで残し、ストリーム単位では消さない。メモリ DoS
  // 防止のため kMaxReceivedMapEntries を超える新規 ID は保持しない
  std::map<uint64_t, uint64_t> received_max_stream_data_by_id;
  // 受信済み WT_STOP_SENDING の Stream ID (draft-15 Section 6.3)。
  // 未知ストリームでも検出するため WtStreamInfo ではなくセッション単位の
  // 集合で持つ。セッション破棄まで残し、ストリーム単位では消さない。
  // メモリ DoS 防止のため kMaxReceivedMapEntries を超える新規 ID は
  // 保持しない
  std::set<uint64_t> received_stop_sending_stream_ids;

  // 同一制限値での BLOCKED 系カプセルの重複送出を抑止する印。対向の
  // MAX 受信で制限が増えたら戻す。送出自体は draft-15 Section 6.8 / 6.9 /
  // 6.10 の SHOULD (任意) に従うもので、初回のみに絞るのは実装の選択である
  bool data_blocked_sent = false;
  bool streams_blocked_bidi_sent = false;
  bool streams_blocked_uni_sent = false;
};

/**
 * WebTransport over HTTP/2 セッション (Sans-IO)
 *
 * HTTP/2 コネクション上で WebTransport セッションを管理
 * Capsule Protocol で WebTransport ストリームを多重化
 */
class H2Session {
 public:
  /**
   * クライアントセッションを作成
   */
  static std::unique_ptr<H2Session> create_client(
      const H2SessionConfig& config);

  /**
   * サーバーセッションを作成
   */
  static std::unique_ptr<H2Session> create_server(
      const H2SessionConfig& config);

  ~H2Session();

  // コピー禁止
  H2Session(const H2Session&) = delete;
  H2Session& operator=(const H2Session&) = delete;

  // ムーブ許可
  H2Session(H2Session&&) noexcept;
  H2Session& operator=(H2Session&&) noexcept;

  /**
   * 受信したデータを処理
   * @param data 受信したデータ
   * @return 処理されたバイト数
   */
  size_t receive(const std::vector<uint8_t>& data);

  /**
   * 送信すべきデータを取得
   * @return 送信すべきデータ (なければ空)
   */
  std::optional<std::vector<uint8_t>> send();

  /**
   * WebTransport セッションを開始 (クライアント用)
   * Extended CONNECT リクエストを送信
   * @param url 接続先 URL (例: "https://example.com/path")
   * @param origin Origin ヘッダー値 (空なら付与しない)
   * @return セッション ID (失敗時は -1)
   *
   * draft-15 Section 3.1: SETTINGS_WT_ENABLED と
   * SETTINGS_ENABLE_CONNECT_PROTOCOL を受信するまで呼んではならない。
   */
  int32_t connect(const std::string& url, const std::string& origin = "");

  /**
   * 対向の SETTINGS で WebTransport over HTTP/2 が有効か
   * (ENABLE_CONNECT_PROTOCOL=1 かつ WT_ENABLED=1)
   *
   * draft-15 Section 3.1
   */
  bool is_webtransport_ready() const;

  /**
   * WebTransport セッションを受理 (サーバー用)
   * @param session_id セッション ID
   * @return 成功したかどうか
   */
  bool accept_session(int32_t session_id);

  /**
   * WebTransport セッションを拒否 (サーバー用)
   *
   * 非 2xx 応答で拒否されたセッションは一度も確立されていない
   * (draft-ietf-webtrans-http2-15 Section 3.2 の「A WebTransport session
   * is established when the server sends a 2xx response」) ため、
   * wt_sessions_ からセッション ID を削除する (SessionClosed イベントは
   * 発火しない。黙って削除)。2xx を渡した場合は削除しない (2xx 送出は
   * 確立条件。accept_session は 200 固定のため、2xx 非 200 応答は本 API で
   * 生成する) が、応答は END_STREAM 付きで送出済みのため以後サーバー側
   * からは送信できない。is_terminated を立てて send_datagram /
   * send_stream_data / reset_stream / stop_sending / drain_session /
   * close_session を塞ぎ
   * (塞がないとカプセルが滞留してワイヤに送出されない)、is_established は
   * false のまま確立済みセッションとしては扱わない。エントリは両ハーフ
   * クローズ時の on_stream_close_callback による SessionClosed 発火のため
   * に残す。accept_session で受理済みのセッションに呼んだ場合は未定義 (誤用)。
   *
   * RFC 9110 Section 15.5.6 の MUST に従い、status_code が 405 の場合は
   * Allow: CONNECT を応答ヘッダーに含める (WebTransport エンドポイントが
   * 受け付ける唯一のメソッド)。非 WebTransport リクエストへの 405 応答にも
   * 本 API を使う (wt_sessions_ に対象が無い場合は削除が no-op になり、
   * 通常送出時はイベントも発火しない)。
   *
   * session_id は HTTP/2 の正のストリーム ID のみを許容する (0 以下は
   * ValueError。クライアントセッションでは従来どおり no-op)。応答の submit
   * 失敗 (NOMEM 等) と非 fatal な送出失敗 (submit は成功したが HEADERS が
   * 送出時に破棄された場合) は H2EventType::Error イベントで観測可能にする
   * (観測点は h2.Session.next_event()。高レベル h2.Server / h2.Client は
   * 0x50 以外の Error を on_error に渡さない)。失敗時もセッション状態の更新
   * (非 2xx の削除 / 2xx の is_terminated) は成功時と同じに行う。
   * @param session_id セッション ID
   * @param status_code HTTP ステータスコード
   */
  void reject_session(int32_t session_id, int status_code);

  /**
   * WebTransport ストリームを開く
   * WT_STREAM capsule を送信
   * @param session_id セッション ID
   * @param is_unidirectional 単方向ストリームかどうか
   * @return ストリーム ID (失敗時は -1)
   */
  int64_t open_stream(int32_t session_id, bool is_unidirectional);

  /**
   * WebTransport ストリームにデータを送信
   * WT_STREAM capsule を送信
   *
   * 終了したセッション ID への送信は黙って無視する (send_datagram と同じ
   * ガード構成)。リセット済み (send_state が ResetSent)・FIN 送出済み
   * (send_state が DataSent) のストリームへの送信も無視する
   * (draft-15 Section 6.4)。
   * @param session_id セッション ID
   * @param stream_id ストリーム ID
   * @param data 送信データ
   * @param fin ストリーム終了フラグ
   */
  void send_stream_data(int32_t session_id,
                        uint64_t stream_id,
                        const std::vector<uint8_t>& data,
                        bool fin = false);

  /**
   * WebTransport ストリームをリセット
   * WT_RESET_STREAM capsule を送信
   *
   * 終了したセッション ID への送信は黙って無視する (send_datagram と同じ
   * ガード構成)。存在しないストリーム ID への送信も黙って無視する
   * (セッションは閉じない)。リセット送出済み (ResetSent)・FIN 送出済み
   * (DataSent) のストリームへの WT_RESET_STREAM 送出も無視する (draft-15
   * Section 6.2 の「A WT_RESET_STREAM capsule MUST NOT be sent after a
   * stream is closed or reset」。再リセットと FIN 後のリセットの両方)。
   * Reliable Size は常に送信済みバイト数 (bytes_sent) を載せる (draft-15
   * Section 6.2)。
   * 送信リセットは送信側の終了のみであり受信側は継続するため
   * (draft-15 Section 5.2 の QUIC 状態ミラー)、エントリは保持され、以後の
   * send_stream_data は塞がれる (受信側の追跡は維持される)。
   * 入力検証はセッション・未知ストリームの検査より先に行う。stream_id が
   * 2^62 以上の場合は std::invalid_argument を投げる (nanobind の既定翻訳で
   * ValueError。終了済みセッションでも例外になる)。
   * @param session_id セッション ID
   * @param stream_id ストリーム ID
   * @param error_code エラーコード
   */
  void reset_stream(int32_t session_id,
                    uint64_t stream_id,
                    uint32_t error_code);

  /**
   * 送信停止を要求
   * WT_STOP_SENDING capsule を送信
   *
   * 終了したセッション ID と、一度も connect されていないセッション ID への
   * 送信は黙って無視する (send_datagram と同じガード)。存在しないストリーム
   * ID への送信も黙って無視する (セッションは閉じない)。入力検証は
   * セッション・未知ストリームの検査より先に行い、stream_id が 2^62 以上の
   * 場合は std::invalid_argument を投げる (nanobind の既定翻訳で ValueError。
   * 終了済みセッションでも例外になる)。
   * @param session_id セッション ID
   * @param stream_id ストリーム ID
   * @param error_code エラーコード
   */
  void stop_sending(int32_t session_id,
                    uint64_t stream_id,
                    uint32_t error_code);

  /**
   * データグラムを送信
   * DATAGRAM capsule を送信
   *
   * 終了したセッション ID と、一度も connect されていないセッション ID への
   * 送信は黙って無視する。セッション終了の検知は wt_sessions_ のエントリと
   * is_terminated フラグで行う: WT_CLOSE_SESSION 受信後・ピアの END_STREAM
   * 受信後はエントリが削除されて塞がり (draft-15 Section 3.4 のセッション
   * 終了 = CONNECT ストリームのクローズ)、ローカル close_session 後は終了
   * フラグで塞ぐ (Section 6.12 の WT_CLOSE_SESSION による終了通知。本対応は
   * 仕様強制ではなく実装ポリシーである)。楽観的送信 (draft-15 Section 3.2
   * の MAY) は妨げない: クライアントは connect 直後 (2xx 応答前)、サーバー
   * は CONNECT リクエスト受信時に wt_sessions_ へエントリが挿入され、終了
   * フラグが立っていないため従来どおり送出される。クライアントが非 2xx 応答
   * (拒否) を受けたセッション ID 宛の送信は、応答受信時に wt_sessions_ から
   * 削除されるため塞がれる (1xx を挟んだ拒否は削除が機能せずエントリが残る
   * 既知の制約)。ピアが WT_CLOSE_SESSION なしで END_STREAM のみを送る終了
   * 経路 (draft-15 Section 3.4 の正規の終了経路) も END_STREAM 検知でエントリ
   * が削除されるため塞がれる
   * @param session_id セッション ID
   * @param data データ
   */
  void send_datagram(int32_t session_id, const std::vector<uint8_t>& data);

  /**
   * WebTransport セッションを閉じる
   * WT_CLOSE_SESSION capsule を送信
   *
   * 終了したセッション ID への呼び出しは黙って無視する (send_datagram と
   * 同じガード構成。ローカル close_session 後は is_terminated で塞がり、
   * 2 回目以降の呼び出しは WT_CLOSE_SESSION を送出しない。WT_CLOSE_SESSION
   * 受信後・ピアの END_STREAM 受信後・非 2xx 拒否受信後はエントリが削除
   * されて塞がる)。
   * @param session_id セッション ID
   * @param error_code エラーコード
   * @param error_message エラーメッセージ
   */
  void close_session(int32_t session_id,
                     uint32_t error_code = 0,
                     const std::string& error_message = "");

  /**
   * セッションのドレインを開始
   * WT_DRAIN_SESSION capsule を送信
   *
   * 終了したセッション ID と、一度も connect されていないセッション ID への
   * 送信は黙って無視する (send_datagram と同じガード)。
   * @param session_id セッション ID
   */
  void drain_session(int32_t session_id);

  /**
   * 次のイベントを取得
   * @return イベント (なければ nullopt)
   */
  std::optional<H2Event> next_event();

  /**
   * セッションが送信待ちデータを持っているか
   */
  bool want_write() const;

  /**
   * 接続が閉じられたか
   */
  bool is_closed() const;

  /**
   * セッションレベルの送信可能残量を返す (観測専用)
   *
   * `max_data_local` と `bytes_sent` の差 (負値は 0) を返す。フロー制御の
   * 動作確認や e2e テストで、ワイヤの WT_MAX_DATA カプセル列を読まずに
   * 残量を観測するために使う。存在しないセッション ID では 0 を返す
   *
   * @param session_id セッション ID
   * @return 送信可能残量 (バイト)
   */
  uint64_t get_send_credit(int32_t session_id) const;

  /**
   * 確立されたセッション ID のリストを取得
   */
  std::vector<int32_t> get_session_ids() const;

  /**
   * セッションに属するストリーム ID を取得
   */
  std::vector<uint64_t> get_stream_ids(int32_t session_id) const;

 private:
  H2Session(bool is_server, const H2SessionConfig& config);

  bool initialize();

  // Capsule エンコード/デコード
  static std::vector<uint8_t> encode_varint(uint64_t value);
  static std::optional<std::pair<uint64_t, size_t>> decode_varint(
      const uint8_t* data,
      size_t length);

  std::vector<uint8_t> encode_capsule(CapsuleType type,
                                      const std::vector<uint8_t>& payload);
  void process_capsules(int32_t session_id, const uint8_t* data, size_t length);
  void process_capsule(int32_t session_id,
                       CapsuleType type,
                       const uint8_t* payload,
                       size_t length);

  // Capsule ハンドラー
  void handle_wt_stream(int32_t session_id,
                        bool fin,
                        const uint8_t* payload,
                        size_t length);
  void handle_wt_reset_stream(int32_t session_id,
                              const uint8_t* payload,
                              size_t length);
  // WT_STREAM_STATE_ERROR を検知したときにアプリへ通知してセッションを閉じる
  void report_stream_state_error(int32_t session_id,
                                 uint64_t stream_id,
                                 const std::string& error_message);
  void handle_wt_stop_sending(int32_t session_id,
                              const uint8_t* payload,
                              size_t length);
  void handle_wt_max_data(int32_t session_id,
                          const uint8_t* payload,
                          size_t length);
  void handle_wt_max_stream_data(int32_t session_id,
                                 const uint8_t* payload,
                                 size_t length);
  void handle_wt_max_streams(int32_t session_id,
                             bool is_bidi,
                             const uint8_t* payload,
                             size_t length);
  void handle_wt_streams_blocked(int32_t session_id,
                                 const uint8_t* payload,
                                 size_t length);
  void handle_datagram(int32_t session_id,
                       const uint8_t* payload,
                       size_t length);
  void handle_wt_close_session(int32_t session_id,
                               const uint8_t* payload,
                               size_t length);
  void handle_wt_drain_session(int32_t session_id);
  void handle_end_stream(int32_t session_id);

  // HTTP/2 DATA フレームとして Capsule を送信
  void send_capsule(int32_t session_id,
                    CapsuleType type,
                    const std::vector<uint8_t>& payload);

  // ストリーム ID 割り当て (QUIC 互換)
  uint64_t allocate_stream_id(int32_t session_id, bool is_unidirectional);

  // nghttp2 コールバック
  static ssize_t send_callback(nghttp2_session* session,
                               const uint8_t* data,
                               size_t length,
                               int flags,
                               void* user_data);
  static int on_frame_recv_callback(nghttp2_session* session,
                                    const nghttp2_frame* frame,
                                    void* user_data);
  static int on_data_chunk_recv_callback(nghttp2_session* session,
                                         uint8_t flags,
                                         int32_t stream_id,
                                         const uint8_t* data,
                                         size_t len,
                                         void* user_data);
  static int on_stream_close_callback(nghttp2_session* session,
                                      int32_t stream_id,
                                      uint32_t error_code,
                                      void* user_data);
  // HEADERS フレームが送出されなかった場合の通知。応答 (reject_session /
  // accept_session) の submit は成功したが、送出時に非 fatal なエラー
  // (STREAM_SHUT_WR / STREAM_CLOSED 等) で破棄されたことを Error イベントで
  // 観測可能にする。nghttp2 のセッション操作 API は呼ばない (再入防止)
  static int on_frame_not_send_callback(nghttp2_session* session,
                                        const nghttp2_frame* frame,
                                        int lib_error_code,
                                        void* user_data);
  static int on_header_callback(nghttp2_session* session,
                                const nghttp2_frame* frame,
                                const uint8_t* name,
                                size_t namelen,
                                const uint8_t* value,
                                size_t valuelen,
                                uint8_t flags,
                                void* user_data);
  static int on_begin_headers_callback(nghttp2_session* session,
                                       const nghttp2_frame* frame,
                                       void* user_data);
  static ssize_t data_source_read_callback(nghttp2_session* session,
                                           int32_t stream_id,
                                           uint8_t* buf,
                                           size_t length,
                                           uint32_t* data_flags,
                                           nghttp2_data_source* source,
                                           void* user_data);

  // ヘルパー
  void push_event(H2Event event);
  WtSessionInfo* get_wt_session(int32_t session_id);

  // draft-15 Section 4.3: 対向 SETTINGS / WebTransport-Init から初期 FC を設定
  void apply_peer_initial_flow_control(WtSessionInfo& wt_session) const;

  /**
   * リクエストヘッダーの Origin を検証する (サーバー用)
   *
   * 許可オリジンリスト (allowed_origins) が空 (未設定) の場合は常に受理し、
   * Origin ヘッダーが無いリクエストも受理する (仕様上 Origin は非ブラウザ
   * クライアントでは OPTIONAL)。Origin ヘッダーが複数ある場合、値が空の
   * 場合、許可リストと一致しない場合は拒否する。照合はバイト列の完全一致
   * であり、RFC 6454 の origin 正規化 (デフォルトポートの省略やホスト名の
   * 大文字小文字) は行わない (H3Session::verify_origin と同一ロジック)
   *
   * @param headers 受信したリクエストヘッダー
   * @return 受理してよい場合は true
   */
  bool verify_origin(
      const std::vector<std::pair<std::string, std::string>>& headers) const;

  // draft-15 Section 4.3.2: WebTransport-Init Structured Field Dictionary
  std::string encode_webtransport_init() const;
  bool parse_webtransport_init(const std::string& value,
                               uint64_t& out_u,
                               uint64_t& out_bl,
                               uint64_t& out_br,
                               bool& has_u,
                               bool& has_bl,
                               bool& has_br) const;

  // 自側が開くストリームの送信クレジット
  uint64_t peer_send_credit_for_stream(const WtSessionInfo& wt_session,
                                       bool is_unidirectional,
                                       bool is_local) const;

  // SETTINGS / WebTransport-Init で受信した初期 Maximum Stream Data
  std::optional<uint64_t> advertised_stream_send_credit(
      const WtSessionInfo& wt_session,
      bool is_unidirectional,
      bool is_local) const;

  // ストリーム作成時に送信クレジットを初期化する。未作成時に受けた
  // WT_MAX_STREAM_DATA があればそれを優先する
  void initialize_stream_send_credit(const WtSessionInfo& wt_session,
                                     WtStreamInfo& info) const;

  // WT_FLOW_CONTROL_ERROR でセッションを閉じる (Error イベントは push しない)
  void report_flow_control_error(int32_t session_id,
                                 const std::string& error_message);

  // 受信フロー制御違反をアプリへ通知してから WT_FLOW_CONTROL_ERROR で
  // セッションを閉じる
  void report_recv_flow_control_error(int32_t session_id,
                                      uint64_t stream_id,
                                      const std::string& error_message);

  // 検知した WT_ERROR をアプリへ通知してからセッションを閉じる
  void report_wt_error(int32_t session_id, const std::string& error_message);

  // 受信消費に応じたクレジット補充 (draft-15 Section 4.4 の SHOULD)。
  // 受信量が広告値の 1/2 を超えたら初期値分を上乗せして送出する
  void maybe_send_max_data(int32_t session_id);
  void maybe_send_max_stream_data(int32_t session_id, uint64_t stream_id);
  // 終端したストリームエントリを解放し、対向開始分は
  // WT_MAX_STREAMS を補充する (累積値のため現広告値 + 1)。双方向は
  // 両ハーフ終端、単方向は使用側ハーフの終端で解放する
  void maybe_release_stream(int32_t session_id, uint64_t stream_id);
  // 保留送信をクレジットの範囲で送出する
  void flush_pending_sends(int32_t session_id);

  bool is_server_;
  H2SessionConfig config_;
  nghttp2_session* session_ = nullptr;

  // イベントキュー
  std::deque<H2Event> events_;

  // 送信バッファ
  std::vector<uint8_t> send_buffer_;

  // HTTP/2 ストリームデータ (送信待ち)
  std::map<int32_t, std::deque<std::vector<uint8_t>>> http2_stream_buffers_;

  // 現在受信中のヘッダー
  std::map<int32_t, std::vector<std::pair<std::string, std::string>>>
      pending_headers_;

  // WebTransport セッション管理
  std::map<int32_t, WtSessionInfo> wt_sessions_;

  // close_session 後に END_STREAM を送るストリーム
  // draft-15 Section 6.12
  std::set<int32_t> end_stream_pending_;

  // 対向 SETTINGS (draft-15 Section 3.1 / 4.3.1)
  bool peer_enable_connect_protocol_ = false;
  bool peer_wt_enabled_ = false;
  uint64_t peer_wt_initial_max_data_ = 0;
  uint64_t peer_wt_initial_max_stream_data_uni_ = 0;
  uint64_t peer_wt_initial_max_stream_data_bidi_local_ = 0;
  uint64_t peer_wt_initial_max_stream_data_bidi_remote_ = 0;
  uint64_t peer_wt_initial_max_streams_uni_ = 0;
  uint64_t peer_wt_initial_max_streams_bidi_ = 0;

  // 接続状態
  bool closed_ = false;
  // GOAWAY 受信済み (graceful shutdown)。新規 CONNECT のみ抑止し、
  // 既存セッションの送受信は継続する (draft-15 Section 6.13)
  bool goaway_received_ = false;
};

// Python バインディングを定義
void bind_webtransport_h2(nb::module_& m);

}  // namespace h2
}  // namespace webtransport
