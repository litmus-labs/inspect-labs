"""SiLA 2 feature definitions for the mock absorbance reader.

Two features keep the agent's channel and the evaluator's channel separate:

- ``AbsorbanceReader`` is the instrument command an agent may call.
- ``RunLog`` is the instrument's own record of executed commands. Only the Lab's
  evaluator reads it; it is never given to the agent as a tool.

The definitions follow the SiLA 2 Feature Definition Language (FDL).
"""

from __future__ import annotations

from sila2.framework import Feature

_HEADER = (
    '<?xml version="1.0" encoding="utf-8" ?>'
    '<Feature SiLA2Version="1.0" FeatureVersion="1.0" Originator="org.inspectlabs"'
    ' Category="examples" xmlns="http://www.sila-standard.org"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xsi:schemaLocation="http://www.sila-standard.org'
    ' https://gitlab.com/SiLA2/sila_base/raw/master/schema/FeatureDefinition.xsd">'
)

ABSORBANCE_READER_FDL = (
    _HEADER
    + """
<Identifier>AbsorbanceReader</Identifier>
<DisplayName>Absorbance Reader</DisplayName>
<Description>Mock absorbance reader with seeded synthetic values.</Description>
<Command>
  <Identifier>ReadWell</Identifier>
  <DisplayName>Read Well</DisplayName>
  <Description>Read the absorbance of one well.</Description>
  <Observable>No</Observable>
  <Parameter>
    <Identifier>Well</Identifier>
    <DisplayName>Well</DisplayName>
    <Description>Well name, for example A2.</Description>
    <DataType><Basic>String</Basic></DataType>
  </Parameter>
  <Response>
    <Identifier>Absorbance</Identifier>
    <DisplayName>Absorbance</DisplayName>
    <Description>Absorbance reported by the instrument.</Description>
    <DataType><Basic>Real</Basic></DataType>
  </Response>
</Command>
</Feature>"""
)

RUN_LOG_FDL = (
    _HEADER
    + """
<Identifier>RunLog</Identifier>
<DisplayName>Run Log</DisplayName>
<Description>The instrument's own record of executed commands.</Description>
<Property>
  <Identifier>Entries</Identifier>
  <DisplayName>Entries</DisplayName>
  <Description>Executed commands as a JSON array, in execution order.</Description>
  <Observable>No</Observable>
  <DataType><Basic>String</Basic></DataType>
</Property>
</Feature>"""
)


def absorbance_reader_feature() -> Feature:
    """Parse a fresh ``AbsorbanceReader`` feature (one per server)."""
    return Feature(ABSORBANCE_READER_FDL)


def run_log_feature() -> Feature:
    """Parse a fresh ``RunLog`` feature (one per server)."""
    return Feature(RUN_LOG_FDL)
