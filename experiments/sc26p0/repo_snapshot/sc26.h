#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <iostream>
#include <fstream>
#include <iomanip>
#include <cfloat>
#include <chrono>

#define SC26_Np 3000 // 円盤の数．


// 初回呼び出し時にタイマーを初期化し，
// 初回呼び出しからの経過時間を秒単位で返す．
double sc26_elapsed_seconds(void)
{
    static std::chrono::high_resolution_clock::time_point SC26_start_time =
        std::chrono::high_resolution_clock::now();

    return std::chrono::duration<double>(
        std::chrono::high_resolution_clock::now()
        - SC26_start_time
    ).count();
}


// 現在の絶対時刻をUNIX時刻として秒単位で返す．
// UNIX時刻は1970年1月1日00時00分00秒（UTC）からの経過秒数である．
double sc26_absolute_seconds(void)
{
    return std::chrono::duration<double>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
}


// 各問題の開始絶対時刻を保存・取得する．
// new_timeが0以上の場合，問題ensの開始時刻を初回のみ保存する．
// new_timeを省略した場合，保存済みの開始時刻を返す．
double sc26_problem_start_time(int ens, double new_time = -1.0)
{
    static double start_time[9] = {};
    static bool recorded[9] = {};

    if(ens < 1 || ens > 8)
        return -1.0;

    if(new_time >= 0.0 && !recorded[ens]){
        start_time[ens] = new_time;
        recorded[ens] = true;
    }

    return start_time[ens];
}


// 入力関数．
// 引数はx座標，y座標，円盤直径，問題番号ens，コマンドライン引数argvである．
void sc26_input_initial_conditions(
    double *x,
    double *y,
    double *diameter,
    int *ens,
    char *argv[]
)
{
    *ens = atoi(argv[1]);

    // 初回呼び出し時に経過時間タイマーを初期化する．（全てのプロセスで）
    sc26_elapsed_seconds();

    // 各問題の開始絶対時刻を初回のみ記録する．（全てのプロセスで）
    sc26_problem_start_time(
        *ens,
        sc26_absolute_seconds()
    );

    int rank = 0;

#ifdef MPI_VERSION
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
#endif

    if(rank == 0){

        char filename[128];
        std::ifstream file;

        sprintf(filename, "input_%d.txt", *ens);
        file.open(filename);

        for(int i = 0; i < SC26_Np; i++)
            file >> x[i] >> y[i] >> diameter[i];

        file.close();
    }

#ifdef MPI_VERSION
    MPI_Bcast(x, SC26_Np, MPI_DOUBLE, 0, MPI_COMM_WORLD);
    MPI_Bcast(y, SC26_Np, MPI_DOUBLE, 0, MPI_COMM_WORLD);
    MPI_Bcast(diameter, SC26_Np, MPI_DOUBLE, 0, MPI_COMM_WORLD);
#endif
}


// 出力関数．
// 引数はx座標，y座標，領域サイズL，問題番号ensである．
// 呼び出すたびに，ファイルの末尾に新しい出力ブロックを追記する．
// 各ブロックの末尾には終端マーカー "END" を書き込み，
// 書き込みが最後まで正常に完了したことを示す．
void sc26_output(
    double *x,
    double *y,
    double L,
    int ens
)
{
    // 保存済みの開始絶対時刻を取得する．
    double start_time = sc26_problem_start_time(ens);

    // 出力関数が呼ばれた時点を終了絶対時刻とする．
    double end_time = sc26_absolute_seconds();

    char filename[128];
    std::ofstream file;

    sprintf(filename, "coord_%d.txt", ens);
    // 追記モードで開く．
    file.open(filename, std::ios::app);

    file << std::setprecision(16);

    file << L << std::endl;
    file << start_time << std::endl;
    file << end_time << std::endl;

    for(int i = 0; i < SC26_Np; i++)
        file << x[i] << " " << y[i] << std::endl;

    // このブロックの書き込みが最後まで完了したことを示す終端マーカー．
    file << "END" << std::endl;

    file.close();
}
