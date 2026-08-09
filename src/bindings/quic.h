/**
 * QUIC バインディング (ngtcp2 ラッパー)
 *
 * Sans-IO スタイルの QUIC 実装
 */

#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/stl/function.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <ngtcp2/ngtcp2.h>
#include <ngtcp2/ngtcp2_crypto.h>
#include <ngtcp2/ngtcp2_crypto_boringssl.h>

#include <openssl/ssl.h>

#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <sys/socket.h>
#include <vector>

namespace nb = nanobind;

namespace webtransport {
namespace quic {

/**
 * QUIC コネクション設定
 */
struct QuicConfig {
  // 最大ストリーム数
  uint64_t max_streams_bidi = 100;
  uint64_t max_streams_uni = 100;

  // 最大データサイズ
  uint64_t max_data = 1048576;
  uint64_t max_stream_data_bidi_local = 262144;
  uint64_t max_stream_data_bidi_remote = 262144;
  uint64_t max_stream_data_uni = 262144;

  // アイドルタイムアウト (ナノ秒)
  uint64_t idle_timeout_ns = 30000000000ULL;  // 30秒

  // ALPN (Application-Layer Protocol Negotiation)
  std::vector<std::string> alpn;

  // サーバー名 (SNI)
  std::string server_name;

  // 証明書関連 (サーバー用)
  std::string cert_file;
  std::string key_file;

  // クライアントのピア証明書検証を行うか
  bool verify_peer = false;

  // CA 証明書ファイル (verify_peer 時、空ならシステム既定パス)
  std::string ca_file;

  // ピア証明書 DER のリストを受け取り、許可なら true
  std::function<bool(const std::vector<std::vector<uint8_t>>&)> verify_callback;

  // Datagram サポート
  bool enable_datagram = true;
  uint64_t max_datagram_frame_size = 65536;

  // 0-RTT / セッションチケット
  bool enable_early_data = true;
  // create 時に import するセッションチケット (DER)
  std::vector<uint8_t> session_ticket;
  // create 時に import する 0-RTT トランスポートパラメータ
  std::vector<uint8_t> early_transport_params;
};

/**
 * QUIC イベント種別
 */
enum class QuicEventType {
  HandshakeCompleted,
  ConnectionClosed,
  StreamData,
  StreamOpened,
  StreamClosed,
  StreamReset,
  DatagramReceived,
  ConnectionIdRetired,
  SessionTicket,
  EarlyDataRejected,
  PathValidated,
  PathValidationFailed,
};

/**
 * QUIC イベント
 */
struct QuicEvent {
  QuicEventType type;
  int64_t stream_id = -1;
  std::vector<uint8_t> data;
  bool fin = false;
  uint64_t error_code = 0;
  std::string reason;
  // STREAM_DATA イベントのストリーム上のオフセット (他イベントでは 0)
  uint64_t offset = 0;
};

/**
 * 送受信 UDP パケット (パス情報付き)
 */
struct QuicPacket {
  std::vector<uint8_t> data;
  std::string local_host;
  uint16_t local_port = 0;
  std::string remote_host;
  uint16_t remote_port = 0;
};

/**
 * ストリームデータ (送信キュー用)
 */
struct StreamData {
  std::vector<uint8_t> data;
  bool fin = false;
};

/**
 * QUIC コネクション (Sans-IO)
 *
 * I/O は Python 側で処理し、このクラスはプロトコル処理のみを行う。
 */
class QuicConnection {
 public:
  /**
   * クライアントとして接続を作成
   *
   * ngtcp2_conn_client_new に渡す path は、以降の receive と同じ
   * アドレスである必要がある。ゼロ path で生成したあと実アドレスで
   * receive するとハンドシェイクが進まない。
   */
  static std::unique_ptr<QuicConnection> create_client(
      const QuicConfig& config,
      const std::string& local_host,
      uint16_t local_port,
      const std::string& remote_host,
      uint16_t remote_port);

  /**
   * サーバーとして接続を作成 (直接作成用)
   */
  static std::unique_ptr<QuicConnection> create_server(
      const QuicConfig& config);

  /**
   * 初期パケットからサーバー接続を作成
   * @param config 設定
   * @param initial_packet クライアントからの初期パケット
   * @param local_host ローカルアドレス
   * @param local_port ローカルポート
   * @param remote_host リモートアドレス
   * @param remote_port リモートポート
   * @return 接続 (失敗時は nullptr)
   *
   * ngtcp2_conn_server_new に渡す path は、直後の receive と同じ
   * アドレスである必要がある。ゼロ初期化 path のまま実アドレスで
   * receive すると NGTCP2_ERR_DROP_CONN になる。
   */
  static std::unique_ptr<QuicConnection> accept(
      const QuicConfig& config,
      const std::vector<uint8_t>& initial_packet,
      const std::string& local_host,
      uint16_t local_port,
      const std::string& remote_host,
      uint16_t remote_port);

  ~QuicConnection();

  // コピー禁止
  QuicConnection(const QuicConnection&) = delete;
  QuicConnection& operator=(const QuicConnection&) = delete;

  // ムーブ許可
  QuicConnection(QuicConnection&&) noexcept;
  QuicConnection& operator=(QuicConnection&&) noexcept;

  /**
   * 受信したデータを処理
   *
   * close() が CONNECTION_CLOSE を生成できた接続 (保持パケットがある状態) では、
   * closed_ 後も受信パケットを処理し、受信パケットへの応答として CONNECTION_CLOSE
   * を再送する (RFC 9000 Section 10.2.1)。このとき ConnectionClosed イベントは
   * push しない (アプリが自ら close() を呼んだため)。close() でパケットを生成
   * できなかった接続、および受信経路で終了した接続では、受信パケットは処理されず
   * 0 を返す。再アーム経路でも ngtcp2 が NGTCP2_ERR_CLOSING を返すため 0 を返す
   * (処理失敗ではなく、受信パケットへの応答として消費した扱い)。
   * @param data 受信した UDP パケット
   * @param local_host ローカルアドレス
   * @param local_port ローカルポート
   * @param remote_host リモートアドレス
   * @param remote_port リモートポート
   * @return 処理されたバイト数
   */
  size_t receive(const std::vector<uint8_t>& data,
                 const std::string& local_host,
                 uint16_t local_port,
                 const std::string& remote_host,
                 uint16_t remote_port);

  /**
   * 送信すべきデータを取得
   *
   * close() が生成した CONNECTION_CLOSE がある場合はそれを返す。初回は必ず
   * 返し、返した後は receive() が受信パケットへの応答 (RFC 9000 Section
   * 10.2.1) として再アームするまで nullopt を返す。
   * @return 送信すべき UDP パケット (なければ nullopt)
   */
  std::optional<QuicPacket> send();

  /**
   * コネクションマイグレーションを開始する (クライアントのみ)
   * @return 成功したら true
   */
  bool initiate_migration(const std::string& local_host,
                          uint16_t local_port,
                          const std::string& remote_host,
                          uint16_t remote_port);

  /**
   * 最後に受信したセッションチケット (DER) を返す
   */
  std::vector<uint8_t> export_session_ticket() const;

  /**
   * ハンドシェイク後の 0-RTT トランスポートパラメータを符号化する
   */
  std::vector<uint8_t> export_0rtt_transport_params() const;

  /**
   * 0-RTT early data が受理されたか
   */
  bool is_early_data_accepted() const;

  /**
   * 0-RTT early data を試みたか
   */
  bool was_early_data_attempted() const;

  /**
   * 次のタイムアウトまでの時間を取得
   * @return タイムアウトまでのナノ秒 (タイムアウトがなければ nullopt)
   */
  std::optional<uint64_t> get_timeout_ns() const;

  /**
   * タイムアウトを処理
   */
  void handle_timeout();

  /**
   * ストリームを開く
   * @param bidirectional 双方向ストリームかどうか
   * @return ストリーム ID (失敗時は -1)
   */
  int64_t open_stream(bool bidirectional = true);

  /**
   * 開設可能な残り双方向ストリーム数を取得
   *
   * ハンドシェイク前はピアのトランスポートパラメータが未受信のため
   * 値が保証されない (クライアントは remote transport params 受信前は 0、
   * サーバーは最初の receive 後にピアの広告値が反映される。0-RTT 時は
   * 古いセッションの広告値が入る)。この前提は ngtcp2 の初期値
   * (max_streams が 0) に依存する。
   * @return 開設可能な残り双方向ストリーム数。コネクションが無いか閉じて
   *   いる場合は nullopt
   */
  std::optional<uint64_t> streams_bidi_left() const;

  /**
   * 開設可能な残り単方向ストリーム数を取得
   *
   * ハンドシェイク前の値の保証は streams_bidi_left と同じ。
   * @return 開設可能な残り単方向ストリーム数。コネクションが無いか閉じて
   *   いる場合は nullopt
   */
  std::optional<uint64_t> streams_uni_left() const;

  /**
   * keep-alive タイムアウトを設定 (ナノ秒)
   *
   * 非ゼロ値を設定すると、接続がアイドルになったあと指定時間経過で
   * keep-alive パケット (PING) が送出され、ピアのアイドルタイムアウトを
   * 延命する (RFC 9000 Section 10.1.2)。UINT64_MAX で無効化する
   * (ngtcp2 のデフォルト)。0 は将来拡張用の予約値のため使用しない
   * (ngtcp2 は 0 を UINT64_MAX 相当として扱う)。
   * @param timeout_ns keep-alive タイムアウト (ナノ秒)
   */
  void keep_alive_timeout(uint64_t timeout_ns);

  /**
   * 鍵更新を開始する
   *
   * ハンドシェイク完了前は False を返す。クライアントはさらに
   * ハンドシェイク完了後の 1RTT パケット書き出し (post-handshake の
   * state 遷移の完了) を必要とする。鍵更新確認前の連続呼び出しは
   * 2 回目が False になる (RFC 9001 Section 6.1 の MUST。ngtcp2 は
   * 鍵更新未確認フラグで実装)。3*PTO の制約 (RFC 9001 Section 6.5 の
   * SHOULD。ngtcp2 はハード制約として実装) は 2 回目以降の鍵更新に
   * 適用される。
   * @return 成功で true
   */
  bool initiate_key_update();

  /**
   * コネクション全体のフロー制御 (max data) を拡張する
   *
   * ピアへは MAX_DATA フレームで通知されるが、未送出の拡張量が
   * window/4 を超えるまでフレームは送出されない。NGTCP2_MAX_VARINT 超の
   * 拡張量はクランプされる。
   * @param datalen 拡張量 (バイト)
   */
  void extend_max_offset(uint64_t datalen);

  /**
   * ストリームのフロー制御 (max stream data) を拡張する
   *
   * 存在しないストリーム ID (ローカル単方向を除く) には 0 (成功) を返す。
   * ローカル単方向ストリーム ID (存在の有無を問わず、負値の単方向 ID も
   * 含む) には NGTCP2_ERR_INVALID_ARGUMENT (False) を返す (ngtcp2 は
   * 存在判定より先にローカル単方向判定を行う)。
   * @param stream_id ストリーム ID
   * @param datalen 拡張量 (バイト)
   * @return 成功で true
   */
  bool extend_max_stream_offset(int64_t stream_id, uint64_t datalen);

  /**
   * 双方向ストリーム上限を拡張する
   *
   * ngtcp2 はストリーム上限を自動では増加させない (明示 API でのみ増加する。
   * stream_open コールバック未発火のまま閉じられたストリームの自動増加の
   * 例外は ngtcp2 の API ドキュメントに記載されている)。ピアへは
   * MAX_STREAMS フレーム (RFC 9000 Section 19.11 の累積制限) として通知される。
   * NGTCP2_MAX_STREAMS 超の拡張数はクランプされる。
   * @param n 拡張するストリーム数
   */
  void extend_max_streams_bidi(uint64_t n);

  /**
   * 単方向ストリーム上限を拡張する
   * NGTCP2_MAX_STREAMS 超の拡張数はクランプされる。
   * @param n 拡張するストリーム数
   */
  void extend_max_streams_uni(uint64_t n);

  /**
   * ストリームにデータを送信
   * @param stream_id ストリーム ID
   * @param data 送信データ
   * @param fin ストリーム終了フラグ
   */
  void send_stream_data(int64_t stream_id,
                        const std::vector<uint8_t>& data,
                        bool fin = false);

  /**
   * ストリームを閉じる (RESET_STREAM + STOP_SENDING)
   * @param stream_id ストリーム ID
   * @param error_code エラーコード (0 なら正常終了)
   */
  void close_stream(int64_t stream_id, uint64_t error_code = 0);

  /**
   * ストリームの読み取り側を閉じる (STOP_SENDING を送出)
   * @param stream_id ストリーム ID
   * @param error_code アプリケーションエラーコード
   */
  void stop_sending(int64_t stream_id, uint64_t error_code = 0);

  /**
   * ストリームの書き込み側をリセットする (RESET_STREAM を送出)
   * @param stream_id ストリーム ID
   * @param error_code アプリケーションエラーコード
   */
  void reset_stream(int64_t stream_id, uint64_t error_code = 0);

  /**
   * Datagram を送信
   * @param data Datagram データ
   */
  void send_datagram(const std::vector<uint8_t>& data);

  /**
   * 接続を閉じる
   * @param error_code エラーコード
   * @param reason 理由
   */
  void close(uint64_t error_code = 0, const std::string& reason = "");

  /**
   * 次のイベントを取得
   * @return イベント (なければ nullopt)
   */
  std::optional<QuicEvent> next_event();

  /**
   * 接続が確立されているか
   */
  bool is_established() const;

  /**
   * 接続が閉じられたか
   */
  bool is_closed() const;

  /**
   * ハンドシェイクが完了したか
   */
  bool is_handshake_completed() const;

  /**
   * 接続 ID を取得
   */
  std::vector<uint8_t> get_connection_id() const;

  /**
   * 接続統計情報 (ngtcp2_conn_info V2) を取得
   *
   * 各フィールドは独立したプロパティとして公開する。latest_rtt / min_rtt /
   * smoothed_rtt / rttvar の単位はナノ秒。cwnd / ssthresh / bytes_in_flight /
   * bytes_sent / bytes_recv / bytes_lost はバイト、pkt_sent / pkt_recv /
   * pkt_lost / ping_recv / pkt_discarded は個数。
   * ハンドシェイク前は ngtcp2 が返す初期値をそのまま返し、None には変換
   * しない。コネクションが無いか閉じている場合のみ nullopt。
   */
  std::optional<uint64_t> latest_rtt() const;
  std::optional<uint64_t> min_rtt() const;
  std::optional<uint64_t> smoothed_rtt() const;
  std::optional<uint64_t> rttvar() const;
  std::optional<uint64_t> cwnd() const;
  std::optional<uint64_t> ssthresh() const;
  std::optional<uint64_t> bytes_in_flight() const;
  std::optional<uint64_t> pkt_sent() const;
  std::optional<uint64_t> bytes_sent() const;
  std::optional<uint64_t> pkt_recv() const;
  std::optional<uint64_t> bytes_recv() const;
  std::optional<uint64_t> pkt_lost() const;
  std::optional<uint64_t> bytes_lost() const;
  std::optional<uint64_t> ping_recv() const;
  std::optional<uint64_t> pkt_discarded() const;

  /**
   * PTO (プローブタイムアウト) を取得 (ナノ秒)
   *
   * @return PTO (ナノ秒)。コネクションが無いか閉じている場合は nullopt
   */
  std::optional<uint64_t> pto() const;

  /**
   * 輻輳ウィンドウ残量を取得 (バイト)
   *
   * @return 輻輳ウィンドウ残量 (バイト)。コネクションが無いか閉じている場合は
   *   nullopt
   */
  std::optional<uint64_t> cwnd_left() const;

  /**
   * コネクション全体のフロー制御残量を取得 (バイト)
   *
   * @return フロー制御残量 (バイト)。コネクションが無いか閉じている場合は
   *   nullopt
   */
  std::optional<uint64_t> max_data_left() const;

  /**
   * ストリームごとのフロー制御残量を取得 (バイト)
   *
   * 存在しないストリームは 0 を返す。
   * @param stream_id ストリーム ID
   * @return フロー制御残量 (バイト)。コネクションが無いか閉じている場合は
   *   nullopt
   */
  std::optional<uint64_t> max_stream_data_left(int64_t stream_id) const;

  /**
   * ストリームの損失パケット数を取得
   *
   * STREAM フレームを含み損失と判定されたパケットの数で、スプリアス損失を
   * 含む場合がある。存在しないストリームは 0 を返す。
   * @param stream_id ストリーム ID
   * @return 損失パケット数。コネクションが無いか閉じている場合は nullopt
   */
  std::optional<uint64_t> stream_loss_count(int64_t stream_id) const;

  /**
   * 送信クォンタムを取得 (バイト)
   *
   * @return 送信クォンタム (バイト)。コネクションが無いか閉じている場合は
   *   nullopt
   */
  std::optional<uint64_t> send_quantum() const;

  /**
   * 現在パスの最大 UDP ペイロードサイズを取得 (バイト)
   *
   * @return 最大 UDP ペイロードサイズ (バイト)。コネクションが無いか閉じて
   *   いる場合は nullopt
   */
  std::optional<uint64_t> path_max_tx_udp_payload_size() const;

  /**
   * コネクションエラーのコードを取得
   *
   * 受信した CONNECTION_CLOSE / APPLICATION_CLOSE フレームでのみ設定される。
   * エラーが無い場合は nullopt。ピアが NO_ERROR (0) の CONNECTION_CLOSE を
   * 送って正常終了した場合も error_code は 0 になり、エラー無し (nullopt) と
   * 区別できない点に注意する。コネクションが閉じた後も取得できる。
   */
  std::optional<uint64_t> error_code() const;

  /**
   * コネクションエラーの理由を取得
   *
   * エラーが無い場合、およびピアが NO_ERROR (0) の CONNECTION_CLOSE を送った
   * 場合も nullopt になる (error_code と連動)。理由が無い場合は空文字。
   * 受信した理由は 1024 バイトに切り詰められる。ピアが送る理由は UTF-8 とは
   * 限らない (RFC 9000 は SHOULD) ため、Python 側では不正バイトを
   * surrogateescape でデコードする。
   */
  std::optional<std::string> reason() const;

  /**
   * TLS 処理時に ngtcp2 が記録した内部エラーコードを取得
   *
   * エラーが無い場合は 0 を返す。
   */
  int tls_error() const;

  /**
   * TLS アラートを取得
   *
   * エラーが無い場合は 0 を返す。
   */
  int tls_alert() const;

  /**
   * リモートのトランスポートパラメータを取得
   *
   * ピアからトランスポートパラメータを受信していない場合は nullopt。
   * 0-RTT 復元時はハンドシェイク完了前でも前回セッションの値が返る。
   * コネクションが閉じた後も取得できる。
   */
  std::optional<uint64_t> remote_max_idle_timeout() const;
  std::optional<uint64_t> remote_max_udp_payload_size() const;
  std::optional<uint64_t> remote_initial_max_data() const;
  std::optional<uint64_t> remote_initial_max_stream_data_bidi_local() const;
  std::optional<uint64_t> remote_initial_max_stream_data_bidi_remote() const;
  std::optional<uint64_t> remote_initial_max_stream_data_uni() const;
  std::optional<uint64_t> remote_initial_max_streams_bidi() const;
  std::optional<uint64_t> remote_initial_max_streams_uni() const;
  std::optional<uint64_t> remote_max_datagram_frame_size() const;

  /**
   * ローカルのトランスポートパラメータを取得
   *
   * ローカルのトランスポートパラメータは常に存在する。コネクションが閉じた
   * 後も取得できる。
   */
  uint64_t local_max_idle_timeout() const;
  uint64_t local_max_udp_payload_size() const;
  uint64_t local_initial_max_data() const;
  uint64_t local_initial_max_stream_data_bidi_local() const;
  uint64_t local_initial_max_stream_data_bidi_remote() const;
  uint64_t local_initial_max_stream_data_uni() const;
  uint64_t local_initial_max_streams_bidi() const;
  uint64_t local_initial_max_streams_uni() const;
  uint64_t local_max_datagram_frame_size() const;

  /**
   * ネゴシエーションされた QUIC バージョンを取得
   *
   * バージョンが確定するまでは 0。
   */
  uint32_t negotiated_version() const;

  /**
   * クライアントが選択した QUIC バージョンを取得
   */
  uint32_t client_chosen_version() const;

  /**
   * CLOSING 状態か
   *
   * ngtcp2 のプロトコル状態を返す。close() が CONNECTION_CLOSE を書き出せた
   * 場合のみ true になる。クライアント Initial 未送信 (クライアント) /
   * Initial 未受信 (サーバー) の時点で close() を呼んだ場合は ngtcp2 が
   * CONNECTION_CLOSE を書けないため false のままになり、is_closed() と
   * 一致しないことがある。ハンドシェイク途中 (Initial 交換済み) の close()
   * は Initial または Handshake パケットで CONNECTION_CLOSE を書けるため
   * true になる。
   */
  bool in_closing_period() const;

  /**
   * DRAINING 状態か
   *
   * ピアの CONNECTION_CLOSE を受信した場合に true になる。受信経路の制約に
   * より、ローカルの close() では true にならない。
   */
  bool in_draining_period() const;

  /**
   * 送信元接続 ID (SCID) の一覧を取得
   *
   * ハンドシェイク前は初期 SCID が 1 個以上含まれる。コネクションが閉じた
   * 後も取得できる。
   */
  std::vector<std::vector<uint8_t>> scid() const;

  /**
   * アクティブな宛先接続 ID (DCID) の一覧を取得
   *
   * ハンドシェイク完了前は空リスト。cid のみを公開し、seq / token 等は
   * 対象外とする。コネクションが閉じた後も取得できる。
   */
  std::vector<std::vector<uint8_t>> active_dcid() const;

 private:
  QuicConnection(bool is_server, const QuicConfig& config);

  bool initialize_client(const std::string& local_host,
                         uint16_t local_port,
                         const std::string& remote_host,
                         uint16_t remote_port);
  bool initialize_server();
  bool initialize_server_from_packet(
      const std::vector<uint8_t>& initial_packet,
      const std::string& local_host,
      uint16_t local_port,
      const std::string& remote_host,
      uint16_t remote_port);

  // サーバー側 0-RTT early data コンテキストを設定する
  bool setup_server_early_data();

  // クライアント側セッションチケット / 0-RTT を SSL に適用する
  bool setup_client_session();

  // 現在パスを ngtcp2_path に書き込む
  void fill_ngtcp2_path(ngtcp2_path* path) const;

  // パスアドレスを更新する
  bool update_path_addresses(const std::string& local_host,
                             uint16_t local_port,
                             const std::string& remote_host,
                             uint16_t remote_port);

  // パスから QuicPacket を組み立てる
  QuicPacket make_packet(const uint8_t* data, size_t len,
                         const ngtcp2_path& path);

  // 接続統計のスナップショットを取得 (コネクションが無いか閉じている場合は nullopt)
  std::optional<ngtcp2_conn_info> get_conn_info() const;

  // リモートのトランスポートパラメータを取得 (未受信なら nullptr)
  const ngtcp2_transport_params* get_remote_transport_params() const;

  // ローカルのトランスポートパラメータを取得
  const ngtcp2_transport_params* get_local_transport_params() const;

  // ngtcp2 コールバック
  static int client_initial_cb(ngtcp2_conn* conn, void* user_data);
  static int recv_crypto_data_cb(ngtcp2_conn* conn,
                                 ngtcp2_encryption_level encryption_level,
                                 uint64_t offset,
                                 const uint8_t* data,
                                 size_t datalen,
                                 void* user_data);
  static int encrypt_cb(uint8_t* dest,
                        const ngtcp2_crypto_aead* aead,
                        const ngtcp2_crypto_aead_ctx* aead_ctx,
                        const uint8_t* plaintext,
                        size_t plaintextlen,
                        const uint8_t* nonce,
                        size_t noncelen,
                        const uint8_t* aad,
                        size_t aadlen);
  static int decrypt_cb(uint8_t* dest,
                        const ngtcp2_crypto_aead* aead,
                        const ngtcp2_crypto_aead_ctx* aead_ctx,
                        const uint8_t* ciphertext,
                        size_t ciphertextlen,
                        const uint8_t* nonce,
                        size_t noncelen,
                        const uint8_t* aad,
                        size_t aadlen);
  static int hp_mask_cb(uint8_t* dest,
                        const ngtcp2_crypto_cipher* hp,
                        const ngtcp2_crypto_cipher_ctx* hp_ctx,
                        const uint8_t* sample);
  static int recv_stream_data_cb(ngtcp2_conn* conn,
                                 uint32_t flags,
                                 int64_t stream_id,
                                 uint64_t offset,
                                 const uint8_t* data,
                                 size_t datalen,
                                 void* user_data,
                                 void* stream_user_data);
  static int acked_stream_data_offset_cb(ngtcp2_conn* conn,
                                         int64_t stream_id,
                                         uint64_t offset,
                                         uint64_t datalen,
                                         void* user_data,
                                         void* stream_user_data);
  static int stream_open_cb(ngtcp2_conn* conn,
                            int64_t stream_id,
                            void* user_data);
  static int stream_close_cb(ngtcp2_conn* conn,
                             uint32_t flags,
                             int64_t stream_id,
                             uint64_t app_error_code,
                             void* user_data,
                             void* stream_user_data);
  static int stream_reset_cb(ngtcp2_conn* conn,
                             int64_t stream_id,
                             uint64_t final_size,
                             uint64_t app_error_code,
                             void* user_data,
                             void* stream_user_data);
  static int recv_datagram_cb(ngtcp2_conn* conn,
                              uint32_t flags,
                              const uint8_t* data,
                              size_t datalen,
                              void* user_data);
  static int handshake_completed_cb(ngtcp2_conn* conn, void* user_data);
  static void rand_cb(uint8_t* dest,
                      size_t destlen,
                      const ngtcp2_rand_ctx* rand_ctx);
  static int get_new_connection_id_cb(ngtcp2_conn* conn,
                                      ngtcp2_cid* cid,
                                      uint8_t* token,
                                      size_t cidlen,
                                      void* user_data);
  static int update_key_cb(ngtcp2_conn* conn,
                           uint8_t* rx_secret,
                           uint8_t* tx_secret,
                           ngtcp2_crypto_aead_ctx* rx_aead_ctx,
                           uint8_t* rx_iv,
                           ngtcp2_crypto_aead_ctx* tx_aead_ctx,
                           uint8_t* tx_iv,
                           const uint8_t* current_rx_secret,
                           const uint8_t* current_tx_secret,
                           size_t secretlen,
                           void* user_data);
  static int recv_retry_cb(ngtcp2_conn* conn,
                           const ngtcp2_pkt_hd* hd,
                           void* user_data);
  static void delete_crypto_aead_ctx_cb(ngtcp2_conn* conn,
                                        ngtcp2_crypto_aead_ctx* aead_ctx,
                                        void* user_data);
  static void delete_crypto_cipher_ctx_cb(ngtcp2_conn* conn,
                                          ngtcp2_crypto_cipher_ctx* cipher_ctx,
                                          void* user_data);
  static int get_path_challenge_data_cb(ngtcp2_conn* conn,
                                        uint8_t* data,
                                        void* user_data);
  static int version_negotiation_cb(ngtcp2_conn* conn,
                                    uint32_t version,
                                    const ngtcp2_cid* client_dcid,
                                    void* user_data);
  static int path_validation_cb(ngtcp2_conn* conn,
                                uint32_t flags,
                                const ngtcp2_path* path,
                                const ngtcp2_path* fallback_path,
                                ngtcp2_path_validation_result res,
                                void* user_data);
  static int tls_early_data_rejected_cb(ngtcp2_conn* conn, void* user_data);

  // BoringSSL セッションチケット受信コールバック
  static int new_session_cb(SSL* ssl, SSL_SESSION* session);

  // BoringSSL カスタム証明書検証コールバック
  static ssl_verify_result_t custom_verify_cb(SSL* ssl, uint8_t* out_alert);

  // ヘルパー
  void push_event(QuicEvent event);
  int write_streams();
  SSL_CTX* create_ssl_ctx();
  int setup_initial_crypto();
  void rebind_conn_ref();

  bool is_server_;
  QuicConfig config_;
  ngtcp2_conn* conn_ = nullptr;
  SSL_CTX* ssl_ctx_ = nullptr;
  SSL* ssl_ = nullptr;
  ngtcp2_crypto_conn_ref conn_ref_;

  // イベントキュー
  std::deque<QuicEvent> events_;

  // ストリームデータ (送信待ち)
  std::map<int64_t, std::deque<StreamData>> stream_buffers_;

  // Datagram 送信キュー
  std::deque<std::vector<uint8_t>> datagram_queue_;

  // 接続状態
  bool handshake_completed_ = false;
  bool closed_ = false;

  // ハンドシェイク完了後の 1RTT パケット (short header) の書き出し記録。
  // クライアントの initiate_key_update ガードに使う (ngtcp2 内部の assert
  // (NGTCP2_CS_POST_HANDSHAKE) 回避のため、post-handshake の state 遷移の
  // 完了が必要。遷移後の 1RTT パケット書き出しはその後にしか起こらない)
  bool post_handshake_write_done_ = false;

  // close() が生成した CONNECTION_CLOSE パケット (初回配送後も保持する)
  // 保持と消費を分離する。close() 時に生成したパケットを保持し続け、
  // send() が返せる状態 (armed) かどうかを close_packet_armed_ で管理する
  std::optional<QuicPacket> pending_close_packet_;

  // pending_close_packet_ を send() が返せる状態かどうか
  // close() 時に true、send() が返すと false、receive() が NGTCP2_ERR_CLOSING
  // を受けると true に戻る (RFC 9000 Section 10.2.1 の closing 状態での応答再送。
  // 再送は受信パケットごとに 1 回で、受信が無ければ再送しない)
  bool close_packet_armed_ = false;

  // 0-RTT 状態
  bool early_data_attempted_ = false;
  bool early_data_rejected_event_pushed_ = false;

  // 最後に受信したセッションチケット (DER)
  std::vector<uint8_t> last_session_ticket_;

  // 現在のパス (receive で更新、send/close で使用)
  sockaddr_storage local_addr_{};
  socklen_t local_addrlen_ = sizeof(sockaddr_in);
  sockaddr_storage remote_addr_{};
  socklen_t remote_addrlen_ = sizeof(sockaddr_in);

  // 現在時刻 (ナノ秒)
  uint64_t timestamp_ns_ = 0;

  // 送信バッファ
  std::vector<uint8_t> send_buffer_;
};

// Python バインディングを定義
void bind_quic(nb::module_& m);

}  // namespace quic
}  // namespace webtransport
