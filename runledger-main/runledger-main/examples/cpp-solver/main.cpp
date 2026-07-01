// A tiny C++ "solver" that emits a #TUNE line on stderr.
// Build: make   (see Makefile)   Run: ./solver --alpha 1.5 --budget 0.2
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <random>
#include <cstdio>

int main(int argc, char** argv) {
    double alpha = 1.0, budget = 1.0;
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::strcmp(argv[i], "--alpha") == 0) alpha = std::atof(argv[i + 1]);
        else if (std::strcmp(argv[i], "--budget") == 0) budget = std::atof(argv[i + 1]);
    }

    std::mt19937 rng(12345);
    std::uniform_real_distribution<double> uni(0.0, 1.0);

    auto t0 = std::chrono::steady_clock::now();
    double score = 0.0;
    for (;;) {
        double elapsed = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - t0).count();
        if (elapsed >= budget) break;
        score = std::max(score, uni(rng) * alpha);
    }
    double elapsed = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - t0).count();

    std::printf("best=%.8f\n", score);
    std::fprintf(stderr, "#TUNE elapsed=%.6f score=%.8f correct=1\n", elapsed, score);
    return 0;
}
