# Convenience aliases. `python demo.py` is the portable entry point and works
# identically on a machine without make.

.PHONY: demo test eval clean

demo:
	python demo.py

test:
	python -m pytest tests/ -q

eval:
	python eval/run_eval.py

clean:
	rm -rf out .scratch .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
