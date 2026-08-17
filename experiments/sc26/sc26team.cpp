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
// ⚠️ 富岳実測 (300秒 / 8問同時 / 各24スレッド、代表3問) で 0.05 が最良と確定。
//    L は dL の決定論的スケジュールで決まる離散値しか取れないため、初期刻みの
//    大きさがそのまま到達点を左右する。0.20 は粗すぎて「大きく飛んで失敗」を
//    繰り返し、0.02 は細かすぎて1段ごとの緩和コストが嵩む。
//      ens1: 0.20→52.9393 / 0.05→52.8988 / 0.02→53.3166
//      ens5: 0.20→53.6428 / 0.05→53.4772 / 0.02→53.8145
//      ens8: 0.20→54.2546 / 0.05→53.7462 / 0.02→53.8145  (φ 0.822→0.838)
//    ⚠️ 各構成 n=1 の測定なので、seed 分散を取ったら再確認すること。
static double DELTA_L_INIT  = 0.05;
static const double DELTA_L_MIN   = 1.0e-7;
static const double ALPHA_INIT    = 0.30;
static const double ALPHA_MAX     = 1.0;
static const double ALPHA_MIN     = 1.0e-13;
static const int    RELAX_STALL   = 400;    // 改善が止まったと判断する連続回数

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
static double sig_max = 0.0;

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
                    const double ddx = pbc(px[i] - px[j], L);
                    const double ddy = pbc(py[i] - py[j], L);
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

    for (int sweep = 0; ; ++sweep) {
        // 膨張系の残り重なりが SAFETY を下回れば、実寸では con-d < 0 = 厳密に非重複。
        // (max_ov <= 0.0 を条件にすると最急降下は漸近的にしか 0 に届かず永久に成立しない)
        if (max_ov < SAFETY) return true;
        if (sc26_elapsed_seconds() > deadline) return false;

        for (int i = 0; i < Np; ++i) {
            tx[i] = wrap(x[i] + alpha * fx[i], L);
            ty[i] = wrap(y[i] + alpha * fy[i], L);
        }
        if (mean_disp(tx, ty, L) > DISP_LIMIT - DISP_MARGIN) {
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

#ifdef _OPENMP
#pragma omp parallel for schedule(static) reduction(+:sum_ov)
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
            if (dr < a_ij) acc += (a_ij - dr);
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

    return finite_ok && std::isfinite(L) && L > 0.0 &&
           mean_ov < 1.0e-10 && disp < DISP_LIMIT;
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

    // ---- 段階2: 圧縮 ⇄ 脱ジャミング を時間いっぱい繰り返す ----
    //   内側 = 貪欲な圧縮 (dL を詰めていき、詰まったら抜ける)
    //   外側 = 詰まったら best を膨張+kick して別 basin から再挑戦
    g_rng = 0x9E3779B97F4A7C15ULL ^ ((uint64_t)ens * 0xBF58476D1CE4E5B9ULL);
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

            // 1試行の締切を切る (失敗 relax に粘らない)
            const double try_deadline = std::min(
                budget, sc26_elapsed_seconds() + std::max(1.0, budget * TRY_FRACTION));
            if (relax(newL, try_deadline) && official_valid(x, y, newL, &so, &dp)) {
                std::copy(x, x + Np, bx); std::copy(y, y + Np, by);
                bestL = newL;
                last_improve = sc26_elapsed_seconds();
                emit(bx, by, bestL, ens, /*force=*/false);
                DBG("[ens" << ens << "] L=" << bestL << " phi=" << area / (bestL * bestL)
                    << " disp=" << dp << " dL=" << dL << " cyc=" << cycle
                    << " t=" << sc26_elapsed_seconds() << "\n");
                dL *= 1.15;                   // 成功が続くなら刻みを戻す
            } else {
                dL *= 0.5;
            }
        }
        if (sc26_elapsed_seconds() >= budget) break;

        // ----- 外側: 脱ジャミング -----
        //   best を膨張 + kick して緩和し、内側の圧縮へ戻す。
        //   best (bx,by,bestL) はここでは一切書き換えない = 失敗しても損しない。
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
    DBG("#TUNE elapsed=" << sc26_elapsed_seconds() << " score=" << -bestL
        << " correct=1 L=" << bestL << " phi=" << area / (bestL * bestL)
        << " ens=" << ens << " seed=" << seed << " perturb=" << (seed ? perturb : 0.0)
        << " blocks=" << g_out_count << " reject=" << g_reject_count << "\n");

    DBG("[ens" << ens << "] FINAL L=" << bestL << " phi=" << area / (bestL * bestL)
        << " t=" << sc26_elapsed_seconds() << " EXPECT_BLOCKS=" << g_out_count
        << " seed=" << seed << " fingerprint=" << std::hex << fp << std::dec << "\n");
    return 0;
}
