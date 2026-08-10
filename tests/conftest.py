def pytest_runtest_logreport(report):
    """Imprime el resultado de cada test cuando termina su fase de ejecucion."""
    if report.when != "call":
        return

    if report.passed:
        status = "PASS"
    elif report.failed:
        status = "FAIL"
    else:
        status = "SKIP"

    print(f"[{status}] {report.nodeid}")