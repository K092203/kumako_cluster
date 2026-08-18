// =====================================================================
// sc26teamXX.cpp — SuperCon2026 本選 ベースライン (1ファイル / 公式I/O接続済)
//
//   構成: セルリスト + 最急降下緩和 + アフィン圧縮 + rollback + anytime 追記出力
//
//   公式 check-results.cpp から読み取った合格条件 (ここが設計の前提):
//     ・overlap 判定は「1ペアあたり」ではなく **Σ max(0, (σi+σj)/2 - Dij) / Np < 1e-10**
//       = 重なり総和 < 3.0e-7。本実装は総和を厳密に 0 にする側で安全側に倒す。
//     ・disp は **< 10.0** (等号不可)。
//     ・time_duration は各問 <= 600 かつ 8問全体 (max end - min start) <= 600。
//     ・採用されるのは締切以内に END まで書けた **最後のブロック**。
//
//   ⚠️ 診断出力は **既定オフ**。開発時だけ -DSC26_DEBUG で有効にする。
//      公式のコンパイル行は固定されていて追加の -D を足せないため、
//      「提出時にフラグで消す」設計にすると消し忘れがそのまま失格につながる。
//      公式ルール: 提出プログラムの入出力は sc26.h の関数のみ。
//
//   ローカル:  g++ -O2 -fopenmp -std=c++17 -DSC26_DEBUG src/sc26team.cpp -o work/solve
//   富岳:      mpiFCCpx -Nclang -Ofast -fopenmp -Rpass=.* -std=c++17 -SSL2BLAMP sc26teamXX.cpp
// =====================================================================
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <iostream>
#include <fstream>
#include <cfloat>
#include <vector>
#include <algorithm>
#include <string>
#include <cstring>
#ifdef _OPENMP
#include <omp.h>
#endif
#include "sc26.h"   // ⚠️ 他のヘッダより後に include する (公式指定)

// ---------------------------------------------------------------------
// 診断出力 (既定オフ。開発時のみ -DSC26_DEBUG)
// ---------------------------------------------------------------------
#ifdef SC26_DEBUG
#define DBG(x) do { std::cerr << x; } while (0)
#else
#define DBG(x) do {} while (0)
#endif

// ---------------------------------------------------------------------
// パラメータ
// ---------------------------------------------------------------------
static const double L0_INIT       = 57.0;   // 初期領域サイズ (問題文)
static const double SAFETY        = 1.0e-7; // 探索時に接触距離へ足す安全マージン
static const double DISP_LIMIT    = 10.0;   // checker は < 10.0 (等号不可)
static const double DISP_MARGIN   = 1.0e-3; // 上限に触れないための余裕
// ⚠️ 富岳実測 (300秒 / 8問同時 / 各24スレッド / 全8問) で決定。
//    L は dL の決定論的スケジュールで決まる離散値しか取れないため、初期刻みが
//    そのまま到達点を左右する。
//
//    ★ **見切り機構 (GIVEUP) の有無で最適値が変わる**。両者に強い相互作用がある。
//      見切り導入前: 失敗1回が 70〜140 秒かかるため、細かい刻みは失敗回数が
//                    増えて損 → 0.04〜0.05 が最良 (ΣL=427.70)
//      見切り導入後: 失敗が安くなり、細かい刻みが有利に転じた → 0.03 が最良
//                    ΣL: 0.05→427.35  0.04→426.95  0.03→**426.85**  (8問中6問で最良)
//
//    ⚠️ したがって GIVEUP を切る/変えるときは dL も測り直すこと。
//       片方だけ変えて「前の最適値」を流用すると必ず外す。
static double DELTA_L_INIT  = 0.03;
static const double DELTA_L_MIN   = 1.0e-7;
static const double ALPHA_INIT    = 0.30;
static const double ALPHA_MAX     = 1.0;
static const double ALPHA_MIN     = 1.0e-13;
static const int    RELAX_STALL   = 400;    // 改善が止まったと判断する連続回数

// ---- 失敗する緩和の早期打ち切り --------------------------------------
//   ⚠️ 富岳の実測で判明: 計算時間の **70〜92% が「失敗する緩和」** に消えていた。
//      圧縮の内訳は 成功17〜19回 / 失敗2〜3回 だが、失敗1回が 70〜140 秒かかる。
//
//   なぜ既存の停滞判定が効かないか:
//      圧縮しすぎた配置では重なりエネルギーが **正の値へ漸近** する。
//      減り続けるので「相対改善 < 1e-13 が400回連続」に到達せず、締切まで走る。
//
//   ⚠️ 時間上限で打ち切る方法は実測で失敗済み (成功するはずの緩和まで殺した)。
//      そこで時間ではなく **中身の信号** で見切る:
//      一定スイープごとにエネルギーを見て、「ほとんど減っていない」かつ
//      「まだ重なりが解消の見込みに遠い」なら、そこから先は無駄と判断する。
static int    GIVEUP_WINDOW = 300;     // 何スイープごとに見るか
static double GIVEUP_GAIN   = 1.0e-3;  // 窓内の相対改善がこれ未満なら見切る
// max_ov が SAFETY のこの倍より大きければ「まだ遠い」と判断する。
//   ⚠️ 当初 100 倍にしていたが厳しすぎた。多分散度の高い ens8 は
//      「そこそこ近いが届かない」状態で延々と粘り、無駄時間が 86% のまま残っていた。
//      富岳 300秒 / 全8問の A/B:
//        FAR=100 → ens8 L=53.9267, 無駄 240.7秒 (見切り1回)
//        FAR=10  → ens8 L=53.6812, 無駄  21.6秒 (見切り2回)  ← 採用
//        FAR=3   → FAR=10 と同値
//      他7問はいずれも同値で退行なし。
static double GIVEUP_FAR    = 10.0;
static bool   GIVEUP_ON     = true;    // A/B 用 (SC26_GIVEUP=0 で従来動作)

// ---- 自己校正する見切り ---------------------------------------------
//   固定閾値には trade-off がある。積極化すると難しい問題 (ens4) は良くなるが
//   簡単な問題 (ens1/2/6/7) が悪化する。富岳実測:
//     GG=1e-3 → ΣL=426.85 (最良)   GG=5e-3 → ΣL=427.02 (ens4 だけ改善)
//   問題ごとに閾値を焼き込むのは本番が別データなので過適合になる。
//
//   そこで **その実行自身の統計** から判断する。成功した緩和が典型的に
//   何スイープで終わるかを覚えておき、今の緩和がその K 倍を超えたら
//   「異常に長い = この L では解消できない」と見なす。
//   問題ごとの定数が要らず、難しい問題でも簡単な問題でも自動で適正化される。
//   ⚠️ 「全成功の平均」を基準にすると失敗する。密度が上がるほど成功する緩和も
//      長くなるため、序盤の平均で後半を測ると正当な緩和まで殺す
//      (ローカル実測: ens4 が 53.6812 → 53.7641 に悪化、見切り7回)。
//      そこで **直近の成功** を基準にする。
//   ⚠️ **富岳の全8問 A/B で却下 (既定は 0 = 無効)**。
//      ローカル ens4 単体では K=4 が僅かに改善したが、富岳の全8問では
//      **8問中7問で悪化**した (ens1 52.8711→53.0255 など)。
//      1問だけの測定は当てにならない、という実例。
//      仕組みは残す (SC26_OK=<K> で再検証できる)。
static double OUTLIER_K  = 0.0;    // 直近成功の何倍で見切るか (0 で無効)

// ---- Failure-State Basin Rescue --------------------------------------
//   実測が示したこと: **律速は計算速度ではなく探索の軌道**。
//     300→570秒 (+90%) でも L は動かず、スイープ +16% でも動かなかった。
//     一方 give-up / dL / FAR のように「どの状態を探索するか」を変えた
//     変更だけが L を動かした。
//
//   そこで、圧縮に失敗した状態 (= overjammed state) を捨てる直前に救出する。
//   旧 escape と違い **Ltry < bestL の状態で救出する**ので、1回成功した
//   瞬間に L 改善になる。膨張してから降り直す必要がない。
//
//   ⚠️ 状態は3つに分ける。best は incumbent であって現在位置ではない。
//      best (bx,by,bestL) には rescue が失敗しても一切触れない。
//
//   ⚠️ rescue にも必ず時間上限を持たせる。「失敗救出に残り全部使う」は
//      今日の朝いちばんで踏んだ罠そのもの。
static int    RESCUE_MODE   = 3;      // 0=無効 / 1=局所kick / 2=失敗後swap / 3=圧縮前swap
//   ⚠️ mode 1 (局所kick) は 277回、mode 2 (失敗後swap) は 535回試して
//      **どちらも0回成功**。合計812回試行して一度も救えていない。
//      疑い: 失敗状態は既に重なりエネルギーの局所最小なので、そこで交換しても
//      緩和が元へ戻すだけではないか。本来の SWAP モンテカルロは
//      **valid な状態で交換する**。mode 3 はそれを試す。
//   ⚠️ mode 1 (局所ランダムkick) は富岳で **277回試して0回成功** し否定済み。
//      mode 2 は多分散系の本命である SWAP: 粒子 i,j の**座標だけ**を交換する。
//      直径は入れ替えないので「大きい円盤が居た場所に小さい円盤が入る」形になり、
//      接触グラフが不連続に変わる。局所kickでは作れない変化。
//      公式 checker は最終座標と各粒子の変位しか見ないので、交換後に
//      平均変位 < 10 を満たせば合法。
static double RESCUE_FRAC   = 0.05;   // 圧力上位のこの割合だけ動かす
static double RESCUE_AMP    = 0.30;   // kick 振幅 (平均直径=1.0 に対する割合)
static double RESCUE_TOTAL  = 0.20;   // rescue 全体に使ってよい予算の割合

// ---- P0: 準備候補の品質を安価に予測できるかの検証 ----------------------
//   Kim & Hilgenfeldt (arXiv:2402.08390) は、φ=1 の overjammed 準安定状態の
//   **エネルギー**と、そこから解凍して得られる臨界充填率 φ_c の間に強い負の相関を
//   報告している (高エネルギー → φ_c≈0.84、低エネルギー → φ_c>0.89)。
//
//   ⚠️ 論文が相関を示したのは「十分緩和された準安定状態のエネルギー」であり、
//      ここで測る E_probe(S) は「固定 S スイープ後の、まだ緩和途中かもしれない
//      エネルギー」である。**同じものではなく、安価な代理指標**として扱う。
//
//   検証したいのは符号を含めた次の関係:
//        E_probe が小さい候補ほど、最終到達 L も小さい
//        → ρ(E_probe, L_final) > 0  を期待する
//
//   ⚠️ 論文の φ 数値を本課題へ直接転用しない。粒径分布・境界条件・変位制約が違う。
static int  PROBE_MODE  = 0;      // 1 で候補評価モード (研究用。提出既定は 0)
static int  PROBE_CAND  = 0;      // 候補番号 (これだけを変えた paired 実験にする)
static double PROBE_WARM = 0.5;   // 予算のこの割合まで通常圧縮してからプローブする
static double DECOMP_STEP = 0.003;   // 粗い走査の刻み。細かさは二分探索で稼ぐ
static int    DECOMP_REFINE = 6;     // 区間を二分する回数 (分解能を上げる)
static double DECOMP_EVAL_SEC = 1.5; // L_c 探索の 1 評価に与える秒数
static double PROBE_OVER  = 0.01;    // probe の過圧縮率 (bestL をこの割合だけ縮める)
static int    PROBE_AUTO  = 1;       // 1 で過圧縮量を自己校正する
static int    PROBE_SWEEPS= 3000;    // probe の固定スイープ数
static bool PRE_SWAP_ON = true;   // 圧縮前 swap
static bool POST_KICK_ON= true;   // 失敗後の rescue (P0 では切って混ぜない)
static double RESCUE_SLICE  = 0.03;   // 1回の rescue に与える時間の割合
static int    RESCUE_TRIES  = 4;      // 1回の失敗につき何回まで救出を試みるか
// 因果分解の 4 群 (SC26_SWMODE):
//   0=D actual  … 抽選 + DCOST判定 + 実際の座標交換 (従来の動作)
//   1=C dryrun  … 抽選と RNG 消費は同じ。**座標交換だけしない**
//   2=B nodraw  … 候補集合の生成までは行うが**抽選もしない** (RNG を消費しない)
//   (A は PRE_SWAP_ON=0 で pre-swap 経路自体を通さない)
// 解釈: A→B = 追加計算の効果 / B→C = RNG 消費の効果 / C→D = 実交換の効果
static int    SWAP_MODE   = 0;
static int    SWAP_SEL    = 0;      // 0=legacy(固定比率) 1=quantile(順位)
static int    SWAP_DRYRUN = 0;      // 後方互換 (SWAP_MODE=1 と同義)
static double SWAP_QUANT  = 0.10;   // 圧力の上位/下位この割合から候補を採る
// ⚠️ 変位コスト上限。**quantile 化の効果を分離するため、まずは元の 4.0 のまま**。
//    「推測で複数箇所を同時に直すと何が効いたか分からない」という原則を守る。
//    DCOST は quantile 化を固定した同一 binary で別途 A/B する (SC26_SDC)。
static double SWAP_DCOST  = 4.0;
static int    RESCUE_NSWAP  = 50;     // 圧縮前swapで1回に交換する組数 (富岳実測で50が最良)

// ---- 後半の代替圧縮スケジュール --------------------------------------
//   実測では最後の改善後に全体の 5〜6 割の時間が残ることがある。そこを
//   同じスケジュールでもう一度掘るのではなく、**別の圧縮履歴**へ回す。
//   今日効いた変更 (dL / 見切り / 距離条件) はすべて「どの L をどの順で試すか」
//   を変えたものなので、これはその延長にある。
//   ⚠️ best は絶対に触らない。負けても Golden は無傷。
static int    ALT_ON      = 0;      // 1 で有効 (提出既定はオフ)
static double ALT_DL0     = 0.035;  // 代替系列の初期刻み (0.03 の反対側。未測定)
static double ALT_FAILMUL = 0.45;   // 失敗時の縮小率 (既定 0.5 と変えて別の L 系列にする)
static double ALT_TRIGGER = 0.35;   // 無改善がこの割合を超えたら代替へ切り替える
//   ⚠️ 失敗そのものが稀 (300秒で2〜4回) なので、1失敗1試行では
//      「一度でも救えるか」を判定できるだけの試行数が集まらない。
//      同じ失敗状態から乱数を変えて複数回試す。
static long long g_last_ok_sweeps = 0;  // 直近に成功した緩和のスイープ数

// ---- 脱ジャミング (候補3: 部分膨張 + kick + 再圧縮) --------------------
//   単一の貪欲な圧縮は「一つの basin」に閉じ込められ、実測では約52秒で
//   改善が止まって残り518秒を捨てていた。φ=0.82〜0.84 はジャミング転移点
//   (0.84〜0.86) の手前なので縮む余地は残っているが、同じ basin では届かない。
//   そこで best を少し膨らませ、乱数で揺さぶり、別の basin から再圧縮する。
//   悪化しても best は書き換えないので、解が悪くなることはない。
//   ⚠️ 変位予算は実測で上限10に対し2.2〜3.1しか使っておらず、kick の原資は潤沢。
// ---- seed 依存の初期摂動 (multi-start の可否を測るために必要) ----------
//   現行ソルバは初期緩和が完全に決定論的なので、replica を何本走らせても
//   同じ解になる。異なる basin へ進ませるための最小限の摂動を入れる。
//   ⚠️ 振幅の単位は「平均直径」= 1.0。実測: 平均直径 0.995〜1.003、
//      最終配置の粒子間隔 ≈0.97 なので、0.05 は間隔の約5%にあたる。
//   ⚠️ seed=0 は摂動なし = **従来の決定論的 baseline と完全に同一**。
//      新機能が baseline を壊さないことを保証するための既定値。
static const double PERTURB_DEFAULT = 0.15;

static double ESCAPE_EXPAND = 0.004;  // bestL をこの割合だけ広げてから揺さぶる
static double ESCAPE_KICK   = 0.12;   // kick 振幅 (直径1.0 に対する割合)
// ⚠️ 「jam した」の判定を dL < 1e-7 にすると、失敗 relax を 21 回繰り返して
//    時間を食い尽くす。実測ではこれが「52秒で改善が止まり518秒を捨てる」の正体だった。
//    同じ basin で dL がこの値を下回ったら、粘らずに脱出した方が得。
static const double JAM_DL        = 5.0e-3;
// jam の本命判定は「時間」。dL は成功のたびに 1.15 倍へ戻るので閾値まで落ちきらず、
// 実測では脱出が一度も発火しなかった。ベストが一定時間改善しなければ jam とみなす。
static const double JAM_IDLE_FRAC = 0.12;   // 予算に対する無改善許容時間の割合
// 圧縮の1試行に与える時間の上限 (予算に対する割合)。
// ⚠️ 実測で **退行と判明したので既定は 1.0 = 実質上限なし**。
//    「失敗する relax に粘らない」つもりで 0.10 を入れたところ、
//    成功するはずの圧縮まで打ち切って L が 52.94 → 53.07 に悪化した
//    (120秒/問題1/8スレッドの A/B)。粘る価値のある緩和の方が多い。
static double TRY_FRACTION  = 1.0;

// ---- 出力頻度の制御 -------------------------------------------------
//   ⚠️ 実機で判明した制約: 1ブロックは約 107KB (3000点 × setprecision(16))。
//      改善のたびに追記すると 8問合計で数十MB になり、LLIO の書き戻しが
//      E1 で失敗して**書いたはずの解が丸ごと消える**(ジョブは正常終了、
//      公式checkerも pass と表示するので気づけない)。
//      よって「意味のある改善」かつ「一定間隔が空いた」ときだけ書く。
//      ただし最初の valid 解と最終ベストは無条件で書く(保険と取りこぼし防止)。
static const double OUT_MIN_REL_GAIN = 0.003;  // 前回書いた L から 0.3% 以上縮んだら
static int    g_out_count   = 0;               // 実際に sc26_output を呼んだ回数
static double g_last_out_L  = -1.0;
static double g_last_out_t  = -1.0e30;
static double g_out_interval = 5.0;            // main で budget から決める

// ---------------------------------------------------------------------
// 状態
// ---------------------------------------------------------------------
static int    Np = SC26_Np;
static double x[SC26_Np], y[SC26_Np];        // 現配置
static double x0_[SC26_Np], y0_[SC26_Np];    // 初期配置 (不変)
static double sig[SC26_Np];                  // 直径 (不変)
static double bx[SC26_Np], by[SC26_Np];      // 暫定ベスト
static double tx[SC26_Np], ty[SC26_Np];      // trial
static double fx[SC26_Np], fy[SC26_Np];      // 押し出し
static double sx_[SC26_Np], sy_[SC26_Np];    // probe 状態の退避 (L_c 探索の起点)
static double sig_max = 0.0;

// ---- 診断カウンタ ----------------------------------------------------
//   「230秒で止まる」の中身を知らずに実装すると手を外す。
//   緩和が遅いのか、圧縮が通らないのか、失敗に時間を食われているのかを
//   切り分けるための計測。探索の挙動そのものは一切変えない。
static long long g_sweeps      = 0;   // 緩和スイープ総数
static double    g_relax_sec   = 0.0; // 緩和に費やした総時間
static double    g_relax_ok_sec= 0.0; //   うち成功した緩和
static long long g_try_ok      = 0;   // 圧縮の成功回数
static long long g_try_ng      = 0;   // 圧縮の失敗回数
static double    g_valid_sec   = 0.0; // official_valid / independent_check の時間
static long long g_giveup      = 0;   // 早期打ち切りした回数
static double    g_last_imp_t  = 0.0; // 最後に best を更新した時刻
static double    g_last_imp_dL = 0.0; // そのときの dL
static long long g_resc_try    = 0;   // rescue を試みた回数
static long long g_resc_ok     = 0;   // rescue が成功した回数
static double    g_resc_sec    = 0.0; // rescue に費やした時間
// 変位制約が本当に律速でないかを確かめる計測。最終配置の変位が 2.7〜3.2 でも、
// 途中の試行でガードに当たっていれば「制約が効いていない」とは言えない。
static long long g_disp_hits   = 0;   // mean_disp ガードが発火した回数
static double    g_max_trial_disp = 0.0; // 試行中に観測した最大の平均変位

// セルリスト
static std::vector<int> cell_head, cell_next;
static int ncell = 0;
static double cell_size = 0.0;

// ---------------------------------------------------------------------
// 周期境界 (問題文 §2.2 の floor 版と厳密に同一)
// ---------------------------------------------------------------------
static inline double pbc(double d, double L) {
    return d - L * std::floor(d / L + 0.5);
}
// 座標が [0,L) に wrap されている前提での高速版。d ∈ (-L, L) なので
// 分岐1回で floor 版と**厳密に同じ値**を返す (d, d±L はいずれも浮動小数で正確)。
// 内側ループはペアごとに2回 pbc を呼ぶため、除算(20〜40サイクル)の除去が効く。
static inline double pbc_fast(double d, double L, double halfL) {
    if (d >  halfL) return d - L;
    if (d < -halfL) return d + L;
    return d;
}
static inline double wrap(double v, double L) {
    v -= L * std::floor(v / L);
    if (v >= L) v -= L;
    if (v < 0.0) v += L;
    return v;
}

// ---------------------------------------------------------------------
// セルリスト構築。セル幅は「最大接触距離」以上にする必要がある。
// ---------------------------------------------------------------------
static void build_cells(const double* px, const double* py, double L) {
    const double need = sig_max + SAFETY;
    ncell = (int)(L / need);
    if (ncell < 3) ncell = 3;              // 3未満だと自セルを二重に見てしまう
    cell_size = L / ncell;

    const int nc2 = ncell * ncell;
    if ((int)cell_head.size() != nc2) cell_head.assign(nc2, -1);
    else std::fill(cell_head.begin(), cell_head.end(), -1);
    if ((int)cell_next.size() != Np) cell_next.assign(Np, -1);

    for (int i = 0; i < Np; ++i) {
        int cx = (int)(px[i] / cell_size); if (cx >= ncell) cx = ncell - 1; if (cx < 0) cx = 0;
        int cy = (int)(py[i] / cell_size); if (cy >= ncell) cy = ncell - 1; if (cy < 0) cy = 0;
        const int c = cx + ncell * cy;
        cell_next[i] = cell_head[c];
        cell_head[c] = i;
    }
}

// ---------------------------------------------------------------------
// 押し出しベクトルと重なり指標を計算する。
//   inflate=true  … 接触距離に SAFETY を足した「膨張系」で見る (探索用)
//   inflate=false … 実寸で見る (公式判定と同じ基準)
// 返り値: E = Σ 1/2 overlap^2 。sum_ov には Σ overlap を入れる (公式指標の分子)。
// セルリストは呼び出し前に build_cells 済みであること。
// 各粒子 i が自分で全近傍を走査する (作用反作用を使わない) ため競合なしで並列化できる。
// ---------------------------------------------------------------------
static double compute(const double* px, const double* py, double L,
                      bool inflate, bool want_force,
                      double* max_ov_out, double* sum_ov_out) {
    const double add = inflate ? SAFETY : 0.0;
    const double halfL = 0.5 * L;
    double E = 0.0, max_ov = 0.0, sum_ov = 0.0;

#ifdef _OPENMP
#pragma omp parallel for schedule(static) reduction(+:E,sum_ov) reduction(max:max_ov)
#endif
    for (int i = 0; i < Np; ++i) {
        double gx = 0.0, gy = 0.0;
        const int cx = std::min((int)(px[i] / cell_size), ncell - 1);
        const int cy = std::min((int)(py[i] / cell_size), ncell - 1);

        for (int dy = -1; dy <= 1; ++dy) {
            int my = cy + dy; if (my < 0) my += ncell; if (my >= ncell) my -= ncell;
            for (int dx = -1; dx <= 1; ++dx) {
                int mx = cx + dx; if (mx < 0) mx += ncell; if (mx >= ncell) mx -= ncell;

                for (int j = cell_head[mx + ncell * my]; j >= 0; j = cell_next[j]) {
                    if (j == i) continue;
                    const double ddx = pbc_fast(px[i] - px[j], L, halfL);
                    const double ddy = pbc_fast(py[i] - py[j], L, halfL);
                    const double d2  = ddx * ddx + ddy * ddy;
                    const double con = 0.5 * (sig[i] + sig[j]) + add;
                    if (d2 >= con * con) continue;

                    const double d  = std::sqrt(d2);
                    const double ov = con - d;
                    if (ov <= 0.0) continue;

                    // 各ペアを i 側と j 側で2回数えるので、集計値は 1/2 を掛ける
                    E      += 0.25 * ov * ov;
                    sum_ov += 0.5  * ov;
                    if (ov > max_ov) max_ov = ov;

                    if (want_force) {
                        double nx, ny;
                        if (d > 1.0e-14) { nx = ddx / d; ny = ddy / d; }
                        else {
                            // 中心がほぼ一致: 決定論的な方向へ逃がす
                            const double th = 2.0 * M_PI *
                                ((double)(((unsigned)(i * 2654435761u) ^ (unsigned)(j * 40503u)) & 0xFFFFu) / 65536.0);
                            nx = std::cos(th); ny = std::sin(th);
                        }
                        gx += ov * nx;
                        gy += ov * ny;
                    }
                }
            }
        }
        if (want_force) { fx[i] = gx; fy[i] = gy; }
    }

    if (max_ov_out) *max_ov_out = max_ov;
    if (sum_ov_out) *sum_ov_out = sum_ov;
    return E;
}

// ---------------------------------------------------------------------
// 平均変位 (公式 calc_disp と同一式。現在の L で PBC を取る)
// ---------------------------------------------------------------------
static double mean_disp(const double* px, const double* py, double L) {
    double s = 0.0;
#ifdef _OPENMP
#pragma omp parallel for schedule(static) reduction(+:s)
#endif
    for (int i = 0; i < Np; ++i) {
        const double dx = pbc(px[i] - x0_[i], L);
        const double dy = pbc(py[i] - y0_[i], L);
        s += std::sqrt(dx * dx + dy * dy);
    }
    return s / Np;
}

// ---------------------------------------------------------------------
// 公式判定と同じ基準で valid か調べる (実寸・平均重なり・平均変位)
// ---------------------------------------------------------------------
static bool official_valid(const double* px, const double* py, double L,
                           double* sum_ov_out, double* disp_out) {
    build_cells(px, py, L);
    double mo = 0.0, so = 0.0;
    compute(px, py, L, /*inflate=*/false, /*want_force=*/false, &mo, &so);
    const double d = mean_disp(px, py, L);
    if (sum_ov_out) *sum_ov_out = so;
    if (disp_out)   *disp_out   = d;
    return (so / Np < 1.0e-10) && (d < DISP_LIMIT);
}

// ---------------------------------------------------------------------
// 緩和: 膨張系の重なりを消しきる。消しきれれば実寸では厳密に非重複。
// ---------------------------------------------------------------------
static bool relax(double L, double deadline) {
    double alpha = ALPHA_INIT;
    int stall = 0;

    build_cells(x, y, L);
    double max_ov = 0.0, sum_ov = 0.0;
    double E = compute(x, y, L, true, true, &max_ov, &sum_ov);

    double win_E = E;        // 窓の開始時のエネルギー
    int    win_at = 0;       // 窓の開始スイープ

    for (int sweep = 0; ; ++sweep) {
        ++g_sweeps;

        // ---- 自己校正した見切り: 成功時の典型を大きく超えたら異常とみなす ----
        if (OUTLIER_K > 0.0 && g_last_ok_sweeps > 0 &&
            sweep > OUTLIER_K * (double)g_last_ok_sweeps) {
            ++g_giveup;
            return false;
        }

        // ---- 失敗の早期打ち切り ----
        //   エネルギーが正の値へ漸近している = この L では解消できない、と判断する。
        if (GIVEUP_ON && sweep - win_at >= GIVEUP_WINDOW) {
            const double gain = (win_E - E) / std::max(win_E, 1.0e-30);
            if (gain < GIVEUP_GAIN && max_ov > GIVEUP_FAR * SAFETY) {
                ++g_giveup;
                return false;   // 見切って呼び出し側へ返す (best は無傷)
            }
            win_E = E; win_at = sweep;
        }
        // 膨張系の残り重なりが SAFETY を下回れば、実寸では con-d < 0 = 厳密に非重複。
        // (max_ov <= 0.0 を条件にすると最急降下は漸近的にしか 0 に届かず永久に成立しない)
        if (max_ov < SAFETY) { g_last_ok_sweeps = std::max<long long>(sweep, 1); return true; }
        if (sc26_elapsed_seconds() > deadline) return false;

        for (int i = 0; i < Np; ++i) {
            tx[i] = wrap(x[i] + alpha * fx[i], L);
            ty[i] = wrap(y[i] + alpha * fy[i], L);
        }
        const double _td = mean_disp(tx, ty, L);
        g_max_trial_disp = std::max(g_max_trial_disp, _td);
        if (_td > DISP_LIMIT - DISP_MARGIN) {
            ++g_disp_hits;
            alpha *= 0.5;
            if (alpha < ALPHA_MIN) return false;
            continue;
        }

        build_cells(tx, ty, L);
        double t_max = 0.0, t_sum = 0.0;
        const double E2 = compute(tx, ty, L, true, true, &t_max, &t_sum);

        if (std::isfinite(E2) && E2 <= E) {
            std::copy(tx, tx + Np, x);
            std::copy(ty, ty + Np, y);
            // fx/fy は trial 側で計算済みなのでそのまま使える
            if (E - E2 < 1.0e-13 * std::max(E, 1.0e-30)) ++stall; else stall = 0;
            E = E2; max_ov = t_max; sum_ov = t_sum;
            alpha = std::min(ALPHA_MAX, alpha * 1.05);
            if (stall >= RELAX_STALL) return false;
        } else {
            alpha *= 0.5;
            if (alpha < ALPHA_MIN) return false;
            // 現配置の力を取り直す
            build_cells(x, y, L);
            E = compute(x, y, L, true, true, &max_ov, &sum_ov);
        }
        (void)sweep;
    }
}

// ---------------------------------------------------------------------
// 決定論的 RNG (xorshift)。提出物は再現性がある方が事故を追いやすいので、
// 時刻ではなく ens 由来の seed から回す。
// ---------------------------------------------------------------------
static uint64_t g_rng = 0;

// ⚠️ **研究用 (probe/candidate) の乱数は Golden の RNG と完全に分離する。**
//    以前 candidate 用に g_rng を早期初期化して 8 個空回ししたところ、
//    PROBE_MODE=0 でも乱数系列がずれ、「既定なら挙動は一切変わらない」という
//    保証が壊れていた。control path を同一 binary 内で保存するために別系統にする。
static uint64_t g_prng = 0;
static inline double prnd01() {
    g_prng ^= g_prng << 13; g_prng ^= g_prng >> 7; g_prng ^= g_prng << 17;
    return (double)(g_prng >> 11) * (1.0 / 9007199254740992.0);
}

static inline double rnd01() {
    g_rng ^= g_rng << 13; g_rng ^= g_rng >> 7; g_rng ^= g_rng << 17;
    return (double)(g_rng >> 11) * (1.0 / 9007199254740992.0);
}

// best 配置を膨張させてから乱数で揺さぶる (= 別の basin へ飛ばす)
static void escape_kick(const double* sx, const double* sy, double srcL,
                        double newL, double amp) {
    const double sc = newL / srcL;
    for (int i = 0; i < Np; ++i) {
        const double th = 2.0 * M_PI * rnd01();
        const double r  = amp * rnd01();
        x[i] = wrap(sx[i] * sc + r * std::cos(th), newL);
        y[i] = wrap(sy[i] * sc + r * std::sin(th), newL);
    }
}

// ---------------------------------------------------------------------
// rescue: 失敗した圧縮状態を、局所的に揺さぶって救出を試みる。
//   全粒子を動かさない。**重なり圧力の高い粒子だけ**を対象にする。
//   圧力の代理として |f_i| を使う (compute が既に計算しているので追加コストゼロ)。
// ---------------------------------------------------------------------
static void rescue_kick(double L) {
    // 圧力上位の閾値を決める (部分ソートを避け、単純に上位割合の値を推定)
    static std::vector<double> mag;
    mag.assign(Np, 0.0);
    for (int i = 0; i < Np; ++i) mag[i] = fx[i] * fx[i] + fy[i] * fy[i];

    std::vector<double> sorted = mag;
    const int k = std::max(1, (int)(RESCUE_FRAC * Np));
    std::nth_element(sorted.begin(), sorted.end() - k, sorted.end());
    const double thr = sorted[sorted.size() - k];

    for (int i = 0; i < Np; ++i) {
        if (mag[i] < thr) continue;
        const double th = 2.0 * M_PI * rnd01();
        const double r  = RESCUE_AMP * std::sqrt(rnd01());
        x[i] = wrap(x[i] + r * std::cos(th), L);
        y[i] = wrap(y[i] + r * std::sin(th), L);
    }
}

// ---------------------------------------------------------------------
// P0 プローブ: 準備候補を固定スイープ数だけ緩和し、途中経過を記録する。
//
//   ⚠️ 壁時計ではなく **スイープ数** で揃える。アーキテクチャ差の影響を受けにくく、
//      Kumako と富岳の結果を同じ土俵で比べられる。
//   1 本の緩和を進めながら複数チェックポイントを取る (別々に走らせ直さない)。
//   これにより「basin の良し悪しを予測するのに最低何スイープ必要か」まで分かる。
// ---------------------------------------------------------------------
static void probe_run(double L, int ens, int cand, const int* ckpt, int nckpt) {
    double alpha = ALPHA_INIT;
    build_cells(x, y, L);
    double max_ov = 0.0, sum_ov = 0.0;
    double E = compute(x, y, L, true, true, &max_ov, &sum_ov);

    const int last = ckpt[nckpt - 1];
    int ci = 0;
    for (int sweep = 0; sweep <= last; ++sweep) {
        if (ci < nckpt && sweep == ckpt[ci]) {
            build_cells(x, y, L);
            double rm = 0.0, rs = 0.0;
            compute(x, y, L, false, false, &rm, &rs);
            const double disp = mean_disp(x, y, L);
            DBG("#PROBE version=1 ens=" << ens << " cand=" << cand << " S=" << sweep
                << " E=" << E << " max_ov=" << max_ov << " sum_ov=" << sum_ov
                << " real_max_ov=" << rm << " real_sum_ov=" << rs
                << " disp=" << disp << " alpha=" << alpha
                << " L=" << L << "\n");
            ++ci;
        }
        if (sweep == last) break;

        for (int i = 0; i < Np; ++i) {
            tx[i] = wrap(x[i] + alpha * fx[i], L);
            ty[i] = wrap(y[i] + alpha * fy[i], L);
        }
        build_cells(tx, ty, L);
        double t_max = 0.0, t_sum = 0.0;
        const double E2 = compute(tx, ty, L, true, true, &t_max, &t_sum);
        if (std::isfinite(E2) && E2 <= E) {
            std::copy(tx, tx + Np, x); std::copy(ty, ty + Np, y);
            E = E2; max_ov = t_max; sum_ov = t_sum;
            alpha = std::min(ALPHA_MAX, alpha * 1.05);
        } else {
            alpha *= 0.5;
            if (alpha < ALPHA_MIN) break;
            build_cells(x, y, L);
            E = compute(x, y, L, true, true, &max_ov, &sum_ov);
        }
    }
}

// ---------------------------------------------------------------------
// rescue mode 2: size-aware coordinate swap
//   「大きくて圧力が高い粒子」と「小さくて圧力が低い粒子」の座標を交換する。
//   大きい円盤が窮屈な場所に居るのが詰まりの原因なので、そこへ小さい円盤を
//   入れ替える。直径は動かさない (規則上も変更禁止)。
//
//   ⚠️ 変位コストは O(1) で見積もれる。交換前後で
//        C_old = d(r_i, r_i^0) + d(r_j, r_j^0)
//        C_new = d(r_j, r_i^0) + d(r_i, r_j^0)
//      なので、予算を食いすぎる交換は事前に弾ける。
// ---------------------------------------------------------------------
// ---- 候補多様性の診断 ----
//   ens1 で 8 候補すべてが完全一致した。閾値が sig_max 依存なので低多分散の
//   問題では候補集合が空になっている、という推測を**実測で確かめる**ため。
//   ⚠️ 推測のまま閾値を直すと、何が効いたか分からなくなる。
static int      g_sw_big = 0, g_sw_small = 0;   // 候補集合の大きさ
static int      g_sw_try = 0, g_sw_acc = 0;     // 交換の試行数・実施数
static uint64_t g_sw_fp  = 1469598103934665603ULL;  // 動かした粒子の指紋
// ---- 全行程の累積 ----
//   ⚠️ NSWAP は「試行回数」であって実施回数ではない。**処置量として見るべきは
//      actual accepted swaps**。Golden が実際に何回の座標置換を行っているかを測る。
static long long g_swc_calls = 0, g_swc_try = 0, g_swc_acc = 0;
static std::vector<unsigned char> g_swc_touched;   // 一度でも動かした粒子

static bool g_use_prng = false;   // true なら probe 用 RNG を使う
static inline double swap_rnd() { return g_use_prng ? prnd01() : rnd01(); }

static void rescue_swap(double L, int nswap) {
    static std::vector<double> press;
    press.assign(Np, 0.0);
    for (int i = 0; i < Np; ++i) press[i] = std::sqrt(fx[i]*fx[i] + fy[i]*fy[i]);

    static std::vector<int> big, small;
    big.clear(); small.clear();

    // ⚠️ 選択則は runtime で切り替える (SC26_SEL)。因果分解の実験では
    //    **legacy に固定**して、RNG 消費と座標交換の効果だけを分離する。
    if (SWAP_SEL == 0) {
        // legacy: sig_max / pmax に対する固定比率。実測で低多分散だと候補集合が壊れる
        //   ens1 big=1925 small=0 / ens4 big=1261 small=4 / ens8 big=377 small=64
        double pmax = 0.0;
        for (int i = 0; i < Np; ++i) pmax = std::max(pmax, press[i]);
        if (pmax <= 0.0) return;
        for (int i = 0; i < Np; ++i) {
            if (press[i] > 0.3 * pmax && sig[i] > sig_max * 0.7) big.push_back(i);
            if (press[i] < 0.05 * pmax && sig[i] < sig_max * 0.6) small.push_back(i);
        }
    } else {
        // quantile: 順位ベース。どの分布でも一定数の候補が得られる
        static std::vector<int> idx;
        idx.resize(Np);
        for (int i = 0; i < Np; ++i) idx[i] = i;
        const int m = std::max(1, (int)(SWAP_QUANT * Np));
        std::nth_element(idx.begin(), idx.begin() + m, idx.end(),
                         [&](int a, int b) { return press[a] > press[b]; });
        big.assign(idx.begin(), idx.begin() + m);
        std::sort(big.begin(), big.end(), [&](int a, int b) { return sig[a] > sig[b]; });
        big.resize(std::max<size_t>(1, big.size() / 2));
        std::nth_element(idx.begin(), idx.begin() + m, idx.end(),
                         [&](int a, int b) { return press[a] < press[b]; });
        small.assign(idx.begin(), idx.begin() + m);
        std::sort(small.begin(), small.end(), [&](int a, int b) { return sig[a] < sig[b]; });
        small.resize(std::max<size_t>(1, small.size() / 2));
    }

    g_sw_big = (int)big.size(); g_sw_small = (int)small.size();
    ++g_swc_calls;
    if ((int)g_swc_touched.size() != Np) g_swc_touched.assign(Np, 0);
    if (big.empty() || small.empty()) return;

    if (SWAP_MODE == 2) return;          // B群: 抽選しない = RNG を消費しない
    for (int k = 0; k < nswap; ++k) {
        ++g_sw_try; ++g_swc_try;
        const int i = big[(size_t)(swap_rnd() * big.size()) % big.size()];
        const int j = small[(size_t)(swap_rnd() * small.size()) % small.size()];
        if (i == j) continue;

        // 変位コストの増分を O(1) で見て、明らかに損な交換は避ける
        const double c_old = std::sqrt(std::pow(pbc(x[i]-x0_[i],L),2) + std::pow(pbc(y[i]-y0_[i],L),2))
                           + std::sqrt(std::pow(pbc(x[j]-x0_[j],L),2) + std::pow(pbc(y[j]-y0_[j],L),2));
        const double c_new = std::sqrt(std::pow(pbc(x[j]-x0_[i],L),2) + std::pow(pbc(y[j]-y0_[i],L),2))
                           + std::sqrt(std::pow(pbc(x[i]-x0_[j],L),2) + std::pow(pbc(y[i]-y0_[j],L),2));
        if (c_new - c_old > SWAP_DCOST) continue;   // 変位を食い過ぎる交換は捨てる

        // ⚠️ 検証用: SWAP_DRYRUN=1 なら**抽選と乱数消費だけ行い、交換はしない**。
        //    Golden の NSWAP 依存が「座標交換の効果」なのか
        //    「乱数系列がずれた効果」なのかを分離するための対照。
        if (SWAP_MODE == 1 || SWAP_DRYRUN) continue;   // C群: 交換しない
        std::swap(x[i], x[j]);
        std::swap(y[i], y[j]);
        ++g_sw_acc; ++g_swc_acc;
        g_swc_touched[i] = 1; g_swc_touched[j] = 1;
        g_sw_fp ^= (uint64_t)(i + 1) * 1099511628211ULL;
        g_sw_fp ^= (uint64_t)(j + 1) * 0x9E3779B97F4A7C15ULL;
    }
}

// ---------------------------------------------------------------------
// ★ 独立検査 — 出力直前の最後の砦
//
//   official_valid() は探索と**同じセルリスト・同じ compute()** を使うため、
//   セルリストにバグがあれば検証も同じバグで見逃す(= 独立していない)。
//   ここではセルリストを一切使わず、全ペア O(N^2) を素朴に回して
//   公式 check-results.cpp と同じ式で判定する。
//
//   コスト: 3000*2999/2 = 約450万ペア。OpenMP 込みで数ミリ秒であり、
//   出力は高々十数回なので無視できる。**間違った解を出すコストの方が桁違いに高い。**
// ---------------------------------------------------------------------
static bool independent_check(const double* px, const double* py, double L,
                              double* sum_ov_out, double* disp_out) {
    double sum_ov = 0.0, sum_disp = 0.0;
    bool finite_ok = true;

    // ⚠️ 判定は **ペアごと** の重なりで見る (新 checker 仕様)。
    //    従来は平均 (総和/Np) だけを見ていた。ペアごとの方が厳しい側なので安全で、
    //    Golden は全ペア overlap = 0 を達成しているためこの変更で退行しない。
    //    最大と総和は 1 回の走査で同時に取る。
    double max_ov_g = 0.0;
#ifdef _OPENMP
#pragma omp parallel for schedule(static) reduction(+:sum_ov) reduction(max:max_ov_g)
#endif
    for (int i = 0; i < Np; ++i) {
        double acc = 0.0;
        for (int j = i + 1; j < Np; ++j) {
            // 公式 check-results.cpp と同じ式をそのまま書く (セルリストを介さない)
            double dx = px[i] - px[j];
            double dy = py[i] - py[j];
            dx -= L * std::floor(dx / L + 0.5);
            dy -= L * std::floor(dy / L + 0.5);
            const double dr   = std::sqrt(dx * dx + dy * dy);
            const double a_ij = 0.5 * (sig[i] + sig[j]);
            const double ov   = a_ij - dr;
            if (ov > max_ov_g) max_ov_g = ov;
            if (ov > 0.0) acc += ov;
        }
        sum_ov += acc;
    }

    for (int i = 0; i < Np; ++i) {
        if (!std::isfinite(px[i]) || !std::isfinite(py[i])) { finite_ok = false; break; }
        double dx = px[i] - x0_[i];
        double dy = py[i] - y0_[i];
        dx -= L * std::floor(dx / L + 0.5);
        dy -= L * std::floor(dy / L + 0.5);
        sum_disp += std::sqrt(dx * dx + dy * dy);
    }

    const double mean_ov = sum_ov / Np;      // 公式の overlap 指標
    const double disp    = sum_disp / Np;
    if (sum_ov_out) *sum_ov_out = mean_ov;
    if (disp_out)   *disp_out   = disp;

    // ⚠️ 判定は **ペアごと** の重なりが 1e-10 未満であること (新 checker 仕様)。
    //    従来は平均 (総和/Np) で見ていたが、こちらの方が厳しい側なので安全。
    //    Golden は全ペア overlap = 0 を達成しているため、この変更で退行しない。
    return finite_ok && std::isfinite(L) && L > 0.0 &&
           max_ov_g < 1.0e-10 && mean_ov < 1.0e-10 && disp < DISP_LIMIT;
}

static int g_reject_count = 0;   // 独立検査で出力を拒否した回数


// ---------------------------------------------------------------------
// 出力ラッパ。force=true なら間隔・改善量を無視して必ず書く。
// 書いた回数を数えておき、ジョブ側が coord ファイルの END 数と突き合わせる。
// ---------------------------------------------------------------------
static void emit(double* px, double* py, double L, int ens, bool force) {
    const double t = sc26_elapsed_seconds();
    if (!force) {
        if (g_last_out_L > 0.0 && (g_last_out_L - L) / g_last_out_L < OUT_MIN_REL_GAIN) return;
        if (t - g_last_out_t < g_out_interval) return;
    }
    if (g_last_out_L > 0.0 && L >= g_last_out_L) return;   // 改善していないなら書かない

    // ★ 出力直前の独立検査。ここを通らないものは絶対に出さない。
    double ind_ov = 0.0, ind_disp = 0.0;
    if (!independent_check(px, py, L, &ind_ov, &ind_disp)) {
        ++g_reject_count;
        DBG("[ens" << ens << "] REJECTED by independent check: L=" << L
            << " mean_overlap=" << ind_ov << " disp=" << ind_disp << "\n");
        return;
    }

    sc26_output(px, py, L, ens);
    ++g_out_count;
    g_last_out_L = L;
    g_last_out_t = t;
    DBG("[ens" << ens << "] WROTE #" << g_out_count << " L=" << L << " t=" << t << "\n");
}

// ---------------------------------------------------------------------
int main(int argc, char** argv) {
    if (argc < 2) { DBG("usage: a.out <ens> [budget_sec]\n"); return 1; }

    // ⚠️ 公式ルール: 変数宣言とメモリ確保を除き、これを最初に実行すること
    int ens = 0;
    sc26_input_initial_conditions(x, y, sig, &ens, argv);

    const double budget = (argc > 2) ? atof(argv[2]) : 570.0;  // 600 から安全マージン

#ifdef SC26_DEBUG
    // ⚠️ P0 の 4 つは **段階1より前** に読む。プローブの分岐が段階1直後にあるため、
    //    従来の読み取り位置 (段階2 の直前) では間に合わなかった。
    if (const char* v = getenv("SC26_PROBE"))   PROBE_MODE    = atoi(v);
    if (const char* v = getenv("SC26_CAND"))    PROBE_CAND    = atoi(v);
    if (const char* v = getenv("SC26_PRESWAP")) PRE_SWAP_ON   = (atoi(v) != 0);
    if (const char* v = getenv("SC26_POSTKICK"))POST_KICK_ON  = (atoi(v) != 0);
    if (const char* v = getenv("SC26_NSWAP"))   RESCUE_NSWAP  = atoi(v);
    if (const char* v = getenv("SC26_PWARM"))   PROBE_WARM    = atof(v);
    if (const char* v = getenv("SC26_DECOMP"))  DECOMP_STEP   = atof(v);
    if (const char* v = getenv("SC26_REFINE"))  DECOMP_REFINE = atoi(v);
    if (const char* v = getenv("SC26_EVALSEC")) DECOMP_EVAL_SEC = atof(v);
    if (const char* v = getenv("SC26_POVER"))   PROBE_OVER    = atof(v);
    if (const char* v = getenv("SC26_PAUTO"))   PROBE_AUTO    = atoi(v);
    if (const char* v = getenv("SC26_PSWEEP"))  PROBE_SWEEPS  = atoi(v);
    if (const char* v = getenv("SC26_SQ"))      SWAP_QUANT    = atof(v);
    if (const char* v = getenv("SC26_SDC"))     SWAP_DCOST    = atof(v);
    if (const char* v = getenv("SC26_DRY"))     SWAP_DRYRUN   = atoi(v);
    if (const char* v = getenv("SC26_SWMODE"))  SWAP_MODE     = atoi(v);
    if (const char* v = getenv("SC26_SEL"))     SWAP_SEL      = atoi(v);
    if (const char* v = getenv("SC26_DL0"))     DELTA_L_INIT  = atof(v);
#endif

    g_out_interval = std::max(3.0, budget / 12.0);   // 予算全体で高々十数ブロックに抑える

    for (int i = 0; i < Np; ++i) { x0_[i] = x[i]; y0_[i] = y[i]; }

    // ---- seed 依存の初期摂動 ----------------------------------------
    //   入力 → 小さな摂動 → 既存の初期緩和 → 既存の圧縮ループ
    //   seed=0 (既定・提出時) は何もしないので baseline と完全に一致する。
    const long long seed    = (argc > 3) ? atoll(argv[3]) : 0;
    const double    perturb = (argc > 4) ? atof(argv[4])  : PERTURB_DEFAULT;
    if (seed != 0) {
        g_rng = 0x9E3779B97F4A7C15ULL ^ ((uint64_t)seed * 0xBF58476D1CE4E5B9ULL)
                                      ^ ((uint64_t)ens  * 0x94D049BB133111EBULL);
        for (int k = 0; k < 8; ++k) rnd01();          // 初期の偏りを流す
        for (int i = 0; i < Np; ++i) {
            const double th = 2.0 * M_PI * rnd01();
            const double r  = perturb * std::sqrt(rnd01());   // 円内一様
            x[i] += r * std::cos(th);
            y[i] += r * std::sin(th);
        }
    }
    for (int i = 0; i < Np; ++i) sig_max = std::max(sig_max, sig[i]);
    double L = L0_INIT;
    for (int i = 0; i < Np; ++i) { x[i] = wrap(x[i], L); y[i] = wrap(y[i], L); }

    double area = 0.0;
    for (int i = 0; i < Np; ++i) area += M_PI * 0.25 * sig[i] * sig[i];
    DBG("[ens" << ens << "] seed=" << seed << " perturb=" << (seed ? perturb : 0.0)
        << " phi0=" << area / (L * L)
        << " sig_max=" << sig_max << " budget=" << budget << "s\n");

    // ---- 段階1: とにかく valid 解を1つ確保する (保険。ここを落とすと無条件0点) ----
    //   L=57 で緩和が間に合わないデータセットが実在する (多分散度が高い側は φ0 が高く、
    //   sig_max も大きいので緩和が重い)。その場合は **L を広げて** 再挑戦する。
    //   L を広げれば φ が下がり緩和は必ず楽になるので、有限時間で必ず valid に到達できる。
    //   悪い L でも「解が無い」より圧倒的にましで、後段の圧縮で取り返せる。
    double so = 0.0, dp = 0.0;
    bool   got = false;
    const double phase1_end = budget * 0.6;
    // 初回は十分な時間を与える。L を広げるのは「本当に緩和しきれない」ときの最後の手段で、
    // 広げた分は後段の圧縮で取り返す必要があるため、安易に発動させると素の L が悪化する。
    double slice = std::max(2.0, budget * 0.35);

    while (!got) {
        relax(L, std::min(phase1_end, sc26_elapsed_seconds() + slice));
        slice = std::max(1.0, budget * 0.05);            // 2回目以降は小刻みに
        if (official_valid(x, y, L, &so, &dp)) { got = true; break; }
        if (sc26_elapsed_seconds() >= phase1_end) break;

        const double wideL = L * 1.02;                   // 2% ずつ広げる
        for (int i = 0; i < Np; ++i) {
            x[i] = wrap(x[i] * (wideL / L), wideL);
            y[i] = wrap(y[i] * (wideL / L), wideL);
        }
        L = wideL;
        DBG("[ens" << ens << "] relax not converged -> widen L=" << L
            << " t=" << sc26_elapsed_seconds() << "\n");
    }

    if (!got) {
        DBG("[ens" << ens << "] NO VALID SOLUTION (fatal)\n");
        return 0;
    }
    std::copy(x, x + Np, bx); std::copy(y, y + Np, by);
    emit(bx, by, L, ens, /*force=*/true);   // 最初の valid は無条件 (保険)
    DBG("[ens" << ens << "] first valid L=" << L << " phi=" << area / (L * L)
        << " disp=" << dp << " t=" << sc26_elapsed_seconds() << "\n");
    double bestL = L;

    // ---- P0: 候補評価モード ----
    //   同じ best 状態から、swap の乱数だけを変えた候補を作り、固定スイープ数だけ
    //   緩和して E_probe 等を記録する。その後そのまま通常の探索へ進んで最終 L を得る。
    //   → 「E_probe は最終 L を予測するか」を paired で測れる。
    // probe 専用 RNG。⚠️ xorshift はゼロから回すと永久にゼロを返すので必ず初期化する
    //    (未初期化のまま rescue_swap を呼び、全候補が 1 ビットも違わなかった)。
    g_prng = 0x9E3779B97F4A7C15ULL ^ ((uint64_t)ens * 0xBF58476D1CE4E5B9ULL)
                                   ^ ((uint64_t)PROBE_CAND * 0x94D049BB133111EBULL);
    for (int k = 0; k < 8; ++k) prnd01();

    if (PROBE_MODE) {
        // ⚠️ 段階1直後 (L≈57, φ≈0.73) でプローブしても候補差は出ない。
        //    その密度では 3000 スイープで完全に解けてしまうため。
        //    **jam 近傍まで通常の圧縮で降りてから**プローブする。
        const double warm = PROBE_WARM * budget;
        double wdL = DELTA_L_INIT;
        while (sc26_elapsed_seconds() < warm && wdL >= JAM_DL) {
            const double nL = bestL - wdL;
            if (nL <= 0.0) break;
            const double ws = nL / bestL;
            for (int i = 0; i < Np; ++i) {
                x[i] = wrap(bx[i] * ws, nL); y[i] = wrap(by[i] * ws, nL);
            }
            if (relax(nL, warm) && official_valid(x, y, nL, &so, &dp)) {
                std::copy(x, x + Np, bx); std::copy(y, y + Np, by);
                bestL = nL; wdL *= 1.15;
            } else wdL *= 0.5;
        }
        DBG("[ens" << ens << "] probe warmup done bestL=" << bestL
            << " phi=" << area/(bestL*bestL) << " t=" << sc26_elapsed_seconds() << "\n");

        // ---- 過圧縮量の自己校正 ----
        //   ⚠️ 固定値を ens ごとに焼き込むと本番の別データで外す。
        //      **現在状態の応答から probe 強度を決める**ので汎化しやすい。
        //   基準は「swap なしで固定 S スイープしても解けきらないが、深すぎない」深さ。
        //   candidate 間の公平性のため、校正は candidate に依存しない
        //   (swap を掛けない状態で行う)。
        double over = PROBE_OVER;
        if (PROBE_AUTO) {
            static const int cal[] = {0, PROBE_SWEEPS};
            for (int k = 0; k < 8; ++k) {
                const double tL = bestL * (1.0 - over);
                const double ts = tL / bestL;
                for (int i = 0; i < Np; ++i) {
                    x[i] = wrap(bx[i] * ts, tL); y[i] = wrap(by[i] * ts, tL);
                }
                build_cells(x, y, tL);
                double cm = 0.0, cs = 0.0;
                compute(x, y, tL, true, true, &cm, &cs);
                probe_run(tL, ens, -1, cal, 2);          // cand=-1 は校正用
                build_cells(x, y, tL);
                double rm = 0.0, rs = 0.0;
                compute(x, y, tL, false, false, &rm, &rs);
                DBG("#PCAL ens=" << ens << " over=" << over
                    << " real_max_ov=" << rm << "\n");
                if (rm <= 0.0)      over *= 1.6;   // 解けてしまった → もっと深く
                else if (rm > 0.05) over /= 1.6;   // 深すぎる         → 浅く
                else break;                         // ちょうど良い
            }
        }
        const double pL = bestL * (1.0 - over);
        const double sc = pL / bestL;
        for (int i = 0; i < Np; ++i) {
            x[i] = wrap(bx[i] * sc, pL);
            y[i] = wrap(by[i] * sc, pL);
        }
        build_cells(x, y, pL);
        double m0 = 0.0, s0 = 0.0;
        compute(x, y, pL, true, true, &m0, &s0);
        g_use_prng = true;
        g_sw_big = g_sw_small = g_sw_try = g_sw_acc = 0;
        g_sw_fp = 1469598103934665603ULL;
        if (PRE_SWAP_ON) rescue_swap(pL, RESCUE_NSWAP);
        DBG("#SWAPDIAG version=1 ens=" << ens << " cand=" << PROBE_CAND
            << " big=" << g_sw_big << " small=" << g_sw_small
            << " try=" << g_sw_try << " accepted=" << g_sw_acc
            << " fp=" << std::hex << g_sw_fp << std::dec
            << " sig_max=" << sig_max << "\n");

        static const int ckpt[] = {0, 50, 100, 300, 1000, 3000};
        probe_run(pL, ens, PROBE_CAND, ckpt, (int)(sizeof(ckpt)/sizeof(ckpt[0])));

        // ---- 候補固有の臨界サイズ L_c を測る ----
        //   ⚠️ 「valid でなければ捨てる」ではP0が成立しない。probe 状態は
        //      意図的に overjammed(まだ重なりが残る)なので、捨てると段階2が
        //      元の basin から始まり、E_probe だけ候補ごとに違って L_final は
        //      同じ、という無意味な実験になる(実際そうなった: adopted=0 が全候補)。
        //
        //   代わりに論文の構図に合わせる:
        //        overjammed 状態 → 制御された解凍 → 初めて valid になる L_c
        //   これが**その候補の basin が持つジャミング点**であり、
        //   E_probe がそれを予測するかが P0 の問い。
        //   Route A を最後まで走らせるより安価なので Kumako での大量取得にも向く。
        //   ⚠️ 「届かなければ -1」にしない。-1 は順位相関に使えないため、
        //      **valid になるまで広げ続ける**。上限に達した場合だけ censored=1 と
        //      して区別し、その時点の L を記録する(打ち切りデータとして扱える)。
        //   ⚠️ 以前は「前の L の状態を引き継いで少しずつ広げる」実装だった。
        //      これは経路依存なので、刻みを変えると比較できなくなる。
        //      → **毎回 probe 状態から直接その L へアフィン展開して緩和する**。
        //      各 L の評価が同じ起点になるので、粗い走査 → 区間の二分、が正当化できる。
        //   ⚠️ 分解能不足は実測で確認済み。刻み 0.0003 では 6 件中 5 件が同値になった。
        double Lc = -1.0, Lc_lo = pL, Lc_hi = -1.0; int censored = 0;
        {
            std::copy(x, x + Np, sx_); std::copy(y, y + Np, sy_);   // 起点を退避

            auto eval_at = [&](double LL) -> bool {
                const double q = LL / pL;
                for (int i = 0; i < Np; ++i) {
                    x[i] = wrap(sx_[i] * q, LL);
                    y[i] = wrap(sy_[i] * q, LL);
                }
                // ⚠️ 1 評価に上限を置く。probe 状態から毎回やり直すので、
                //    ここを切らないと粗い走査だけで予算を使い切る (実測: censored=2 連発)。
                return relax(LL, std::min(budget, sc26_elapsed_seconds() + DECOMP_EVAL_SEC))
                       && official_valid(x, y, LL, &so, &dp);
            };

            // 粗い走査で valid になる区間を挟む
            double L2 = pL;
            while (sc26_elapsed_seconds() < budget) {
                if (eval_at(L2)) { Lc_hi = L2; break; }
                Lc_lo = L2;
                if (L2 >= bestL * 1.10) { Lc = L2; censored = 1; break; }
                L2 *= (1.0 + DECOMP_STEP);
            }

            if (censored == 0 && Lc_hi > 0.0) {
                // 区間 [Lc_lo, Lc_hi] を二分して分解能を上げる。
                // 各評価は同じ起点からなので、経路依存で順位が壊れない。
                for (int it = 0; it < DECOMP_REFINE && sc26_elapsed_seconds() < budget; ++it) {
                    const double mid = 0.5 * (Lc_lo + Lc_hi);
                    if (mid <= Lc_lo || mid >= Lc_hi) break;
                    if (eval_at(mid)) Lc_hi = mid; else Lc_lo = mid;
                }
                Lc = Lc_hi;
            }
            if (Lc < 0.0) { Lc = L2; censored = 2; }   // 予算切れ
        }
        int adopted = 0;
        if (Lc > 0.0 && Lc < bestL) {          // 候補が best を破ったときだけ採用
            std::copy(x, x + Np, bx); std::copy(y, y + Np, by);
            bestL = Lc; adopted = 1;
            emit(bx, by, bestL, ens, /*force=*/false);
        }
        g_use_prng = false;
        DBG("#PROBEEND version=1 ens=" << ens << " cand=" << PROBE_CAND
            << " probeL=" << pL << " Lc=" << Lc
            << " Lc_lo=" << Lc_lo << " Lc_hi=" << Lc_hi
            << " Lc_res=" << (Lc_hi > 0 && Lc_lo > 0 ? Lc_hi - Lc_lo : DECOMP_STEP)
            << " refine=" << DECOMP_REFINE
            << " phi_c=" << (Lc > 0 ? area/(Lc*Lc) : 0.0)
            << " censored=" << censored
            << " adopted=" << adopted
            << " t=" << sc26_elapsed_seconds() << "\n");
    }

    // ---- 段階2: 圧縮 ⇄ 脱ジャミング を時間いっぱい繰り返す ----
    //   内側 = 貪欲な圧縮 (dL を詰めていき、詰まったら抜ける)
    //   外側 = 詰まったら best を膨張+kick して別 basin から再挑戦
    int cycle = 0;

    double jam_idle_frac = JAM_IDLE_FRAC;
    // ⚠️ 脱ジャミング(候補3)は実測で貪欲な圧縮に負けたため **既定オフ**。
    //    負けた理由は「52秒で改善が止まる」という前提が誤りだったこと。
    //    実際には 246 秒時点でも改善が続いており、jam 検出器が生産的な圧縮を
    //    強制中断していた。コードは残してあるので SC26_ESCAPE=1 で再検証できる。
    bool escape_on = false;
#ifdef SC26_DEBUG
    // 開発時だけ環境変数でパラメータを振れるようにする (掃引用)。
    // 提出ビルドでは SC26_DEBUG が無いのでこのブロックごと消える。
    if (const char* v = getenv("SC26_KICK"))   ESCAPE_KICK   = atof(v);
    if (const char* v = getenv("SC26_EXPAND")) ESCAPE_EXPAND = atof(v);
    if (const char* v = getenv("SC26_IDLE"))   jam_idle_frac = atof(v);
    if (const char* v = getenv("SC26_TRYFRAC")) TRY_FRACTION  = atof(v);
    if (const char* v = getenv("SC26_ESCAPE"))  escape_on     = (atoi(v) != 0);
    if (const char* v = getenv("SC26_DL0"))     DELTA_L_INIT  = atof(v);
    if (const char* v = getenv("SC26_GIVEUP"))  GIVEUP_ON     = (atoi(v) != 0);
    if (const char* v = getenv("SC26_GW"))      GIVEUP_WINDOW = atoi(v);
    if (const char* v = getenv("SC26_GG"))      GIVEUP_GAIN   = atof(v);
    if (const char* v = getenv("SC26_GF"))      GIVEUP_FAR    = atof(v);
    if (const char* v = getenv("SC26_OK"))      OUTLIER_K     = atof(v);
    if (const char* v = getenv("SC26_RESC"))    RESCUE_MODE   = atoi(v);
    if (const char* v = getenv("SC26_RFRAC"))   RESCUE_FRAC   = atof(v);
    if (const char* v = getenv("SC26_RAMP"))    RESCUE_AMP    = atof(v);
    if (const char* v = getenv("SC26_RTRY"))    RESCUE_TRIES  = atoi(v);
    if (const char* v = getenv("SC26_NSWAP"))   RESCUE_NSWAP  = atoi(v);
    if (const char* v = getenv("SC26_ALT"))     ALT_ON        = atoi(v);
    if (const char* v = getenv("SC26_ALTDL"))   ALT_DL0       = atof(v);
    if (const char* v = getenv("SC26_ALTFM"))   ALT_FAILMUL   = atof(v);
#endif
    const double jam_idle = escape_on ? std::max(3.0, budget * jam_idle_frac) : 1.0e30;
    DBG("[ens" << ens << "] escape=" << (escape_on ? "on" : "off")
        << " kick=" << ESCAPE_KICK << " expand=" << ESCAPE_EXPAND
        << " idle=" << jam_idle << "\n");
    double last_improve = sc26_elapsed_seconds();

    while (sc26_elapsed_seconds() < budget) {
        // ----- 内側: 圧縮 -----
        double dL = DELTA_L_INIT;
        while (sc26_elapsed_seconds() < budget && dL >= JAM_DL
               && sc26_elapsed_seconds() - last_improve < jam_idle) {
            const double newL = bestL - dL;
            if (newL <= 0.0) break;

            const double s = newL / bestL;
            for (int i = 0; i < Np; ++i) {
                x[i] = wrap(bx[i] * s, newL);
                y[i] = wrap(by[i] * s, newL);
            }

            // ---- mode 3: **valid 側で** swap してから圧縮する ----
            //   失敗状態(局所最小)で交換しても緩和が元へ戻すだけ、という疑いへの対処。
            //   本来の SWAP モンテカルロは平衡状態で交換する。
            //   best は触らず、圧縮した作業状態 x,y の上でだけ交換する。
            if (RESCUE_MODE == 3 && PRE_SWAP_ON && g_try_ng > 0) {
                build_cells(x, y, newL);
                double _m = 0.0, _s2 = 0.0;
                compute(x, y, newL, true, true, &_m, &_s2);   // 圧力(=|f|)を得る
                rescue_swap(newL, RESCUE_NSWAP);
                ++g_resc_try;
            }

            // 1試行の締切を切る (失敗 relax に粘らない)
            const double try_deadline = std::min(
                budget, sc26_elapsed_seconds() + std::max(1.0, budget * TRY_FRACTION));
            const double _t0 = sc26_elapsed_seconds();
            const bool _rok = relax(newL, try_deadline);
            const double _t1 = sc26_elapsed_seconds();
            g_relax_sec += _t1 - _t0;
            if (_rok) g_relax_ok_sec += _t1 - _t0;
            const bool _vok = _rok && official_valid(x, y, newL, &so, &dp);
            g_valid_sec += sc26_elapsed_seconds() - _t1;
            if (_vok) {
                ++g_try_ok;
                std::copy(x, x + Np, bx); std::copy(y, y + Np, by);
                bestL = newL;
                last_improve = sc26_elapsed_seconds();
                g_last_imp_t = last_improve; g_last_imp_dL = dL;
                emit(bx, by, bestL, ens, /*force=*/false);
                DBG("[ens" << ens << "] L=" << bestL << " phi=" << area / (bestL * bestL)
                    << " disp=" << dp << " dL=" << dL << " cyc=" << cycle
                    << " t=" << sc26_elapsed_seconds() << "\n");
                dL *= 1.15;                   // 成功が続くなら刻みを戻す
            } else {
                ++g_try_ng;

                // ---- rescue: best には一切触れずに、失敗状態から救出を試みる ----
                //   このとき x,y は「圧縮して緩和に失敗した状態」= overjammed state。
                //   同じ失敗状態を tx,ty に退避し、乱数を変えて複数回試す。
                bool rescued = false;
                if (RESCUE_MODE != 0 && POST_KICK_ON && g_resc_sec < RESCUE_TOTAL * budget
                    && sc26_elapsed_seconds() < budget) {
                    const double _r0 = sc26_elapsed_seconds();
                    std::copy(x, x + Np, tx); std::copy(y, y + Np, ty);   // 失敗状態を退避

                    for (int rt = 0; rt < RESCUE_TRIES && !rescued; ++rt) {
                        if (sc26_elapsed_seconds() >= budget) break;
                        if (g_resc_sec + (sc26_elapsed_seconds() - _r0) >= RESCUE_TOTAL * budget) break;

                        std::copy(tx, tx + Np, x); std::copy(ty, ty + Np, y);  // 毎回同じ地点から
                        build_cells(x, y, newL);
                        double _mo = 0.0, _so = 0.0;
                        compute(x, y, newL, true, true, &_mo, &_so);   // fx,fy を最新に
                        if (RESCUE_MODE == 2) rescue_swap(newL, RESCUE_NSWAP);
                        else                  rescue_kick(newL);
                        const double _rdl = std::min(budget,
                            sc26_elapsed_seconds() + std::max(1.0, budget * RESCUE_SLICE));
                        ++g_resc_try;
                        if (relax(newL, _rdl) && official_valid(x, y, newL, &so, &dp)) {
                            ++g_resc_ok;
                            std::copy(x, x + Np, bx); std::copy(y, y + Np, by);
                            bestL = newL;
                            last_improve = sc26_elapsed_seconds();
                            g_last_imp_t = last_improve; g_last_imp_dL = dL;
                            emit(bx, by, bestL, ens, false);
                            DBG("[ens" << ens << "] RESCUED L=" << bestL << " disp=" << dp
                                << " try=" << rt << " t=" << last_improve << "\n");
                            rescued = true;
                            dL *= 1.15;
                        }
                    }
                    g_resc_sec += sc26_elapsed_seconds() - _r0;
                }
                if (!rescued) dL *= 0.5;
            }
        }
        if (sc26_elapsed_seconds() >= budget) break;

        // ----- 後半の代替スケジュール -----
        //   best からもう一度、**別の刻み系列**で降り直す。best は触らない。
        if (ALT_ON && sc26_elapsed_seconds() - last_improve > ALT_TRIGGER * budget) {
            double adL = ALT_DL0;
            DBG("[ens" << ens << "] ALT start dL0=" << adL
                << " t=" << sc26_elapsed_seconds() << "\n");
            while (sc26_elapsed_seconds() < budget && adL >= JAM_DL) {
                const double aL = bestL - adL;
                if (aL <= 0.0) break;
                const double as = aL / bestL;
                for (int i = 0; i < Np; ++i) {
                    x[i] = wrap(bx[i] * as, aL);
                    y[i] = wrap(by[i] * as, aL);
                }
                if (relax(aL, budget) && official_valid(x, y, aL, &so, &dp)) {
                    std::copy(x, x + Np, bx); std::copy(y, y + Np, by);
                    bestL = aL;
                    last_improve = sc26_elapsed_seconds();
                    g_last_imp_t = last_improve; g_last_imp_dL = adL;
                    emit(bx, by, bestL, ens, false);
                    DBG("[ens" << ens << "] ALT improved L=" << bestL
                        << " t=" << last_improve << "\n");
                    adL *= 1.15;
                } else {
                    adL *= ALT_FAILMUL;
                }
            }
            continue;
        }

        // ----- 外側: 脱ジャミング -----
        //   ⚠️ **既定オフのときは何もせず抜ける。**
        //      以前は escape_on が jam_idle の時間判定を無効化するだけで、
        //      dL < JAM_DL で内側を抜けるとこのブロックが**無条件に実行**されていた。
        //      しかも relax(escL, budget) の締切が予算まるごとなので、
        //      残り時間を食い潰しえた (診断は "escape=off" と表示するので気づけない)。
        //   ⚠️ さらに、成功しても内側ループが x[i]=wrap(bx[i]*s,newL) で **best から
        //      作り直す**ため、脱出して得た配置は誰にも使われず捨てられていた。
        //      「別 basin から再圧縮する」という設計意図が実装で成立していない。
        //      有効化して使うなら、まずこの受け渡しを直す必要がある。
        if (!escape_on) break;

        ++cycle;
        const double escL = bestL * (1.0 + ESCAPE_EXPAND);
        escape_kick(bx, by, bestL, escL, ESCAPE_KICK);

        if (!relax(escL, budget) || !official_valid(x, y, escL, &so, &dp)) {
            DBG("[ens" << ens << "] escape#" << cycle << " relax失敗 → best から再試行"
                << " t=" << sc26_elapsed_seconds() << "\n");
            last_improve = sc26_elapsed_seconds();   // ⚠️ ここを忘れると内側が即抜けて空転する
            continue;   // best は無傷なので、次のサイクルで別の乱数を引き直す
        }
        // 脱出できたので、内側の圧縮に改めて時間を与える
        last_improve = sc26_elapsed_seconds();
        DBG("[ens" << ens << "] escape#" << cycle << " L=" << escL
            << " disp=" << dp << " t=" << sc26_elapsed_seconds() << "\n");
    }

    // 最終ベストを必ず書き出す (間隔制限で取りこぼしていた分の回収)
    emit(bx, by, bestL, ens, /*force=*/true);

    // ⚠️ ジョブ側がこの数と coord_<ens>.txt の END 行数を突き合わせる。
    //    食い違ったら LLIO が書き戻しに失敗している = 解が消えている。
    // 配置の指紋。seed が本当に探索経路を分岐させたかを機械的に確認するため。
    uint64_t fp = 1469598103934665603ULL;
    for (int i = 0; i < Np; ++i) {
        uint64_t bits;
        double v = bx[i] + 1024.0 * by[i];
        std::memcpy(&bits, &v, sizeof(bits));
        fp ^= bits; fp *= 1099511628211ULL;
    }
    // kumako_cluster が集計に使う1行 (開発ビルドのみ。提出版では出ない)。
    //   score は「大きいほど良い」慣習なので -L を入れる。L/phi/seed/指紋も併記する。
    DBG("#DIAGMETA schema=1 solver=sc26team ens=" << ens << "\n");
    DBG("#DIAG version=1 sweeps=" << g_sweeps
        << " relax_sec=" << g_relax_sec
        << " relax_ok_sec=" << g_relax_ok_sec
        << " relax_ng_sec=" << (g_relax_sec - g_relax_ok_sec)
        << " valid_sec=" << g_valid_sec
        << " try_ok=" << g_try_ok << " try_ng=" << g_try_ng
        << " giveup=" << g_giveup
        << " last_imp_t=" << g_last_imp_t << " last_imp_dL=" << g_last_imp_dL
        << " idle_after_last_imp=" << (sc26_elapsed_seconds() - g_last_imp_t)
        << " resc_try=" << g_resc_try << " resc_ok=" << g_resc_ok
        << " resc_sec=" << g_resc_sec
        << " disp_hits=" << g_disp_hits << " max_trial_disp=" << g_max_trial_disp
        << " sw_calls=" << g_swc_calls << " sw_try=" << g_swc_try
        << " sw_acc=" << g_swc_acc
        << " sw_touched=" << [](){ long long n=0; for (unsigned char c : g_swc_touched) n += c; return n; }()
        << " sw_acc_per_call=" << (g_swc_calls ? (double)g_swc_acc / g_swc_calls : 0.0)
        << " sweeps_per_sec=" << (g_sweeps / std::max(1.0, sc26_elapsed_seconds()))
        << " ens=" << ens << "\n");

    DBG("#TUNE elapsed=" << sc26_elapsed_seconds() << " score=" << -bestL
        << " correct=1 L=" << bestL << " phi=" << area / (bestL * bestL)
        << " ens=" << ens << " seed=" << seed << " perturb=" << (seed ? perturb : 0.0)
        << " blocks=" << g_out_count << " reject=" << g_reject_count << "\n");

    DBG("[ens" << ens << "] FINAL L=" << bestL << " phi=" << area / (bestL * bestL)
        << " t=" << sc26_elapsed_seconds() << " EXPECT_BLOCKS=" << g_out_count
        << " seed=" << seed << " fingerprint=" << std::hex << fp << std::dec << "\n");
    return 0;
}
