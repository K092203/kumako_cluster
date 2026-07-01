CXX ?= c++
CXXFLAGS ?= -O2 -std=c++17

build/solver: main.cpp
	mkdir -p build
	$(CXX) $(CXXFLAGS) -o $@ main.cpp

.PHONY: clean
clean:
	rm -rf build
