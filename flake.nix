{
  description = "toyscene - C++ scene model, reflect-cpp JSON/schema, generated Pydantic models, nanobind boundary";

  inputs = {
    # Pinned to the nixos-25.11 release rev. Exact pin matters here: the
    # nixpkgs-unstable rev from the same day ships python312Packages.anyio 4.14.2
    # and black 26.5.1, neither of which is in the binary cache and both of which
    # fail their own test suites, taking datamodel-code-generator and
    # scikit-build-core down with them.
    nixpkgs.url = "github:NixOS/nixpkgs/b6018f87da91d19d0ab4cf979885689b469cdd41";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAll (pkgs:
        let
          pyEnv = pkgs.python312.withPackages (ps: with ps; [
            numpy                      # 2.3.4
            pydantic                   # 2.11.7
            datamodel-code-generator   # 0.35.0
            pytest                     # 8.4.2
            nanobind                   # 2.9.2
            scikit-build-core          # 0.11.5
            # build frontend, so `pip install --no-build-isolation -e .` works
            pip
            setuptools
            wheel
          ]);
        in
        {
          default = pkgs.mkShell {
            name = "toyscene";

            packages = [
              pkgs.gcc14          # 14.3.0, C++20
              pkgs.cmake
              pkgs.ninja
              pkgs.glm            # 1.0.3, header-only; provides the glm::glm target
              pkgs.git            # FetchContent needs it
              pyEnv
            ];

            shellHook = ''
              # reflect-cpp is not in nixpkgs; CMake FetchContent pulls the pinned tag
              # into .deps so repeated configures do not re-clone.
              export FETCHCONTENT_BASE_DIR="$PWD/.deps"
              export CMAKE_GENERATOR=Ninja

              # nixpkgs site-packages is read-only, so the editable install lives in a
              # venv that inherits the nix Python packages.
              if [ ! -x .venv/bin/python ]; then
                echo "toyscene: creating .venv (--system-site-packages)"
                ${pyEnv}/bin/python -m venv --system-site-packages .venv
              fi
              source .venv/bin/activate

              echo "toyscene devShell | $(python --version) | $(g++ --version | head -1)"
              echo "  build:      pip install --no-build-isolation -e ."
              echo "  regenerate: ./scripts/generate_models.sh"
              echo "  test:       pytest -q    &&    ctest --test-dir build-cpp"
            '';
          };
        });
    };
}
