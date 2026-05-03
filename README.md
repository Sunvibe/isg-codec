# isg-codec

Python library for encoding and decoding the proprietary ISG protocol used by Stiebel Eltron and Tecalor heat pumps.

This repository is intended for VibeCoding with Codex GPT-5.5 Middle.

## Purpose

The project aims to understand as many device protocols as possible that can be connected to a Stiebel Eltron ISG web.

## Long-Term Goal

The long-term goal is to publish this project as a PyPI package that can be installed with pip.

The core library should stay focused and avoid dependencies that are only needed for protocol discovery, decoding, and device support work.

## Protocol Discovery Tools

Supporting new devices may require separate test and decoding tools. These tools can have their own dependencies, such as connectivity through the currently available ser2net setup and, in the future, optional ESPHome-based access.

## Repository Structure

- `src/isg_codec/` contains the core library code that should remain suitable for PyPI distribution.
- `tools/` contains development tools for capturing, inspecting, and understanding recorded frames before they become supported decoder logic.
- `tests/` contains automated tests for the library.
- `tests/fixtures/` contains captured frame samples used to make decoder behavior reproducible.

## Inspiration

Inspired by [Sunvibe/tecalor-thz5-5-eco-homeassistant-bridge](https://github.com/Sunvibe/tecalor-thz5-5-eco-homeassistant-bridge).
