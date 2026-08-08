# Releasing

1. Ensure `CHANGELOG.md` contains the release date and version.
2. Set the same version in `pyproject.toml`.
3. Run the complete local CI commands and a clean-account AWS acceptance test.
4. Configure a PyPI project and GitHub `pypi` environment for PyPI Trusted
   Publishing, scoped to `.github/workflows/release.yml`.
5. Merge through a protected branch after review.
6. Create and push a signed tag such as `v0.5.0`.

The release workflow builds and validates the wheel and source archive,
publishes them to PyPI with OIDC, and attaches them to a GitHub release. Protect
the release environment with required reviewers for the first production
release.
