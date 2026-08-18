#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <iostream>
#include <fstream>
#include <cfloat>
#include <string>

#define thresh 2.0
#define Nn  1000
#define Np  3000
#define ens_max 8

// coord_*.txt(追記形式)を読み込み，各問題について
// 8問全体の締切(t_start_min + 600秒)以内かつ完全に書き込まれた
// 最後のブロックを有効な回答として採用する．
void input(double (*x)[Np], double (*y)[Np],
           double (*x0)[Np], double (*y0)[Np],
           double (*diameter)[Np],
           double *time_start, double *time_end,
           int ens, int *M, double *L, bool *valid,
           double *t_start_min)
{
  char filename[128];
  std::ifstream file;
  int l;

  // --- 1st pass: 8問全体で共有される締切時刻 t_cutoff を求める ---
  // 各問題 l の start_time は，参加者がその問題を解き始めたタイミングに
  // よって問題ごとに異なり得るため，8つの coord_l.txt それぞれの
  // 先頭ブロックから start_time を読み取り，その最小値を求める．
  *t_start_min = DBL_MAX;
  {
    double tmp_L, tmp_start, tmp_end;

    for(l = 1; l <= ens_max; l++){
      sprintf(filename, "coord_%d.txt", l);
      file.open(filename);

      if(!file){
        std::cerr << "cannot open " << filename << std::endl;
        continue;
      }

      if(file >> tmp_L >> tmp_start >> tmp_end){
        if(tmp_start < *t_start_min)
          *t_start_min = tmp_start;
      }

      file.close();
    }
  }

  // 8問全体で共有される締切時刻(個々の start_time ではなく，
  // 8問の最小 start_time を基準にする)．
  double t_cutoff = *t_start_min + 600.0;

  // --- 2nd pass: 各問題について，締切以内かつ完全に書き込まれた
  //     最後のブロックを有効な回答として採用する ---
  for(l = 1; l <= ens_max; l++){
    sprintf(filename, "coord_%d.txt", l);
    file.open(filename);

    valid[l] = false;

    double blk_L, blk_start, blk_end;
    static double bx[Np], by[Np]; // 読み込み用の一時バッファ

    while(file >> blk_L >> blk_start >> blk_end){
      bool block_ok = true;

      // 座標データ Np 個分をすべて正常に読み込めるか確認する．
      for(int i = 0; i < Np; i++){
        if(!(file >> bx[i] >> by[i])){
          block_ok = false;
          break;
        }
      }

      // 座標データの後に終端マーカー "END" が続いているか確認する．
      // これにより，数値の桁が途中で欠けたまま構文的にはパース
      // 可能な状態でファイルが終わっている場合(書き込み中断の
      // 典型的なケース)も，不完全なブロックとして検出できる．
      if(block_ok){
        std::string marker;

        if(!(file >> marker) || marker != "END"){
          block_ok = false;
        }
      }

      if(!block_ok){
        // 不完全なブロックを検出．このブロックは破棄し，
        // 直前に確定している完全なブロックをそのまま維持する．
        // ファイル末尾が壊れている可能性が高いため，これ以上は読まない．
        break;
      }

      if(blk_end <= t_cutoff){
        // 締切以内かつ完全なブロック：現時点で最新の有効な回答として
        // 確定データを上書きする．
        L[l] = blk_L;
        time_start[l] = blk_start;
        time_end[l]   = blk_end;

        for(int i = 0; i < Np; i++){
          x[l][i] = bx[i];
          y[l][i] = by[i];
        }

        valid[l] = true;
      }
      else{
        // 締切を超えたブロック：追記は時系列順である前提のため，
        // これ以降のブロックもすべて締切超過とみなし読み込みを打ち切る．
        break;
      }
    }

    if(valid[l])
      M[l] = (int)(L[l] / thresh);

    file.close();
  }

  for(l = 1; l <= ens_max; l++){
    sprintf(filename, "input_%d.txt", l);
    file.open(filename);

    for(int i = 0; i < Np; i++)
      file >> x0[l][i] >> y0[l][i] >> diameter[l][i];

    file.close();
  }
}


int f(int i, int M)
{
  int k;

  k = i;

  if(k < 0)
    k += M;

  if(k >= M)
    k -= M;

  return k;
}


void cell_list(int (*list)[Nn],
               double (*x)[Np],
               double (*y)[Np],
               int M,
               double L,
               int ens)
{
  int i, j, k;
  int nx, ny;
  int l, m;
  double dx, dy, r2;

  int (*map)[Nn] = new int[M * M][Nn];

  for(i = 0; i < M; i++)
    for(j = 0; j < M; j++)
      map[i + M * j][0] = 0;

  for(i = 0; i < Np; i++){
    nx = f((int)(x[ens][i] * M / L), M);
    ny = f((int)(y[ens][i] * M / L), M);

    for(m = ny - 1; m <= ny + 1; m++){
      for(l = nx - 1; l <= nx + 1; l++){
        int c = f(l, M) + M * f(m, M);

        map[c][map[c][0] + 1] = i;
        map[c][0]++;
      }
    }
  }

  for(i = 0; i < Np; i++){
    list[i][0] = 0;

    nx = f((int)(x[ens][i] * M / L), M);
    ny = f((int)(y[ens][i] * M / L), M);

    for(k = 1; k <= map[nx + M * ny][0]; k++){
      j = map[nx + M * ny][k];

      if(j > i){
        dx = x[ens][i] - x[ens][j];
        dy = y[ens][i] - y[ens][j];

        dx -= L * floor((dx + 0.5 * L) / L);
        dy -= L * floor((dy + 0.5 * L) / L);

        r2 = dx * dx + dy * dy;

        if(r2 < thresh * thresh){
          list[i][0]++;
          list[i][list[i][0]] = j;
        }
      }
    }
  }

  delete [] map;
}


int calc_disp(double (*x)[Np],
              double (*y)[Np],
              double (*x0)[Np],
              double (*y0)[Np],
              double *mean_disp,
              double *L,
              bool *valid)
{
  double dx, dy;

  for(int l = 1; l <= ens_max; l++){
    mean_disp[l] = 0.0;

    if(!valid[l])
      continue; // 有効なブロックが無い問題は計算をスキップ

    for(int i = 0; i < Np; i++){
      dx = x[l][i] - x0[l][i];
      dy = y[l][i] - y0[l][i];

      dx -= L[l] * floor(dx / L[l] + 0.5);
      dy -= L[l] * floor(dy / L[l] + 0.5);

      mean_disp[l] += sqrt(dx * dx + dy * dy) / Np;
    }
  }

  return 0;
}


int calc_overlap(double (*x)[Np],
                 double (*y)[Np],
                 double *overlap,
                 bool *overlap_violation,
                 double (*a)[Np],
                 double *L,
                 int *M,
                 int (*list)[Nn],
                 bool *valid)
{
  double dx, dy, dr, a_ij;
  int overlap_pairs;

  for(int l = 1; l <= ens_max; l++){

    overlap[l] = 0.0;
    overlap_violation[l] = false;

    if(!valid[l])
      continue; // 有効なブロックが無い問題は計算をスキップ

    cell_list(list, x, y, M[l], L[l], l);

    overlap_pairs = 0;

    for(int i = 0; i < Np; i++){
      for(int j = 1; j <= list[i][0]; j++){
        int k = list[i][j];

        dx = x[l][k] - x[l][i];
        dy = y[l][k] - y[l][i];

        dx -= L[l] * floor(dx / L[l] + 0.5);
        dy -= L[l] * floor(dy / L[l] + 0.5);

        dr = sqrt(dx * dx + dy * dy);
        a_ij = 0.5 * (a[l][i] + a[l][k]);

        if(dr < a_ij){
          overlap[l] += a_ij - dr;
          overlap_pairs++;

          if(a_ij - dr >= 1.e-10)
            overlap_violation[l] = true;
        }
      }
    }

    // 重なっている粒子対についての平均overlap量にする．
    if(overlap_pairs > 0)
      overlap[l] /= overlap_pairs;

  }

  return 0;
}


void output(double *L, double (*a)[Np],
            double *time_start, double *time_end,
            double *overlap, double *disp,
            bool *overlap_violation,
            bool *valid, double t_start_min)
{
  char filename[128];
  std::ofstream file;

  double phi[ens_max + 1] = {};
  double Area[ens_max + 1] = {};
  double time_duration[ens_max + 1] = {};

  sprintf(filename, "results.txt");
  file.open(filename);

  for(int j = 1; j <= ens_max; j++){

    if(!valid[j]){
      // 締切以内に完全なブロックが一つも無い場合は無条件でfail．
      file << j << " "
           << "N/A N/A N/A N/A N/A fail(no valid block)"
           << std::endl;

      std::cout << j << " "
                << "N/A N/A N/A N/A N/A fail(no valid block)"
                << std::endl;

      continue;
    }

    for(int i = 0; i < Np; i++)
      Area[j] += M_PI * 0.25 * a[j][i] * a[j][i];

    phi[j] = Area[j] / L[j] / L[j];

    time_duration[j] = time_end[j] - time_start[j];

    if(time_duration[j] <= 600.0 &&
       overlap[j] < 1.e-10 &&
       !overlap_violation[j] &&
       disp[j] < 10.0){

      file << j << " "
           << L[j] << " "
           << phi[j] << " "
           << time_duration[j] << " "
           << overlap[j] << " "
           << disp[j] << " "
           << "pass"
           << std::endl;

      std::cout << j << " "
                << L[j] << " "
                << phi[j] << " "
                << time_duration[j] << " "
                << overlap[j] << " "
                << disp[j] << " "
                << "pass"
                << std::endl;
    }
    else{
      file << j << " "
           << L[j] << " "
           << phi[j] << " "
           << time_duration[j] << " "
           << overlap[j] << " "
           << disp[j] << " "
           << "fail"
           << std::endl;

      std::cout << j << " "
                << L[j] << " "
                << phi[j] << " "
                << time_duration[j] << " "
                << overlap[j] << " "
                << disp[j] << " "
                << "fail"
                << std::endl;
    }
  }

  // 全体の実行時間チェック．
  // 8問全体の最初の開始時刻 t_start_min から，
  // 有効な回答のうち最も遅い終了時刻までを全体の実行時間とする．
  double max = -DBL_MAX;
  bool any_valid = false;

  for(int j = 1; j <= ens_max; j++){
    if(!valid[j])
      continue;

    any_valid = true;

    if(max < time_end[j])
      max = time_end[j];
  }

  if(any_valid){
    double total_time_duration = max - t_start_min;

    if(total_time_duration <= 600.0){
      file << "total_time_duration "
           << total_time_duration << " pass"
           << std::endl;

      std::cout << "total_time_duration "
                << total_time_duration << " pass"
                << std::endl;
    }
    else{
      file << "total_time_duration "
           << total_time_duration << " fail"
           << std::endl;

      std::cout << "total_time_duration "
                << total_time_duration << " fail"
                << std::endl;
    }
  }
  else{
    file << "total_time_duration N/A fail(no valid block)"
         << std::endl;

    std::cout << "total_time_duration N/A fail(no valid block)"
              << std::endl;
  }

  file.close();
}


int main()
{
  double (*diameter)[Np] = new double[ens_max + 1][Np];
  double (*x)[Np]        = new double[ens_max + 1][Np];
  double (*y)[Np]        = new double[ens_max + 1][Np];
  double (*x0)[Np]       = new double[ens_max + 1][Np];
  double (*y0)[Np]       = new double[ens_max + 1][Np];

  int (*list)[Nn] = new int[Np][Nn];

  int *M = new int[ens_max + 1];
  double *L = new double[ens_max + 1];
  double *time_start = new double[ens_max + 1];
  double *time_end = new double[ens_max + 1];
  double *overlap = new double[ens_max + 1];
  bool *overlap_violation = new bool[ens_max + 1];
  double *mean_disp = new double[ens_max + 1];
  bool *valid = new bool[ens_max + 1];

  double t_start_min;

  input(x, y, x0, y0, diameter,
        time_start, time_end, 0, M, L, valid,
        &t_start_min);

  calc_overlap(x, y, overlap, overlap_violation,
               diameter, L, M, list, valid);

  calc_disp(x, y, x0, y0,
            mean_disp, L, valid);

  output(L, diameter, time_start, time_end,
         overlap, mean_disp, overlap_violation,
         valid, t_start_min);

  delete[] diameter;
  delete[] x;
  delete[] y;
  delete[] x0;
  delete[] y0;
  delete[] list;

  delete[] M;
  delete[] L;
  delete[] time_start;
  delete[] time_end;
  delete[] overlap;
  delete[] overlap_violation;
  delete[] mean_disp;
  delete[] valid;

  return 0;
}
