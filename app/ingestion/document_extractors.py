"""Local format adapters. Optional libraries load only for their format."""
import csv
from datetime import date, datetime, time
from importlib.metadata import version
import re
import unicodedata

from app.ingestion.document_packages import require


def normalized(text, prose=False):
    text = unicodedata.normalize('NFC', text).replace('\r\n', '\n').replace('\r', '\n')
    return '\n'.join(re.sub(r'[^\S\n]+', ' ', line).strip() for line in text.split('\n')).strip() if prose else text


def config(value):
    require(isinstance(value, dict) and not set(value) - {'encoding', 'delimiter', 'tables', 'ocr_mode', 'ocr_language', 'ocr_dpi', 'tessdata'}, 'extract_config_fields')
    result = {'encoding': 'utf-8-sig', 'delimiter': ',', 'tables': [],
              'ocr_mode': 'auto', 'ocr_language': 'vie+eng', 'ocr_dpi': 300, 'tessdata': None} | value
    require(isinstance(result['encoding'], str) and isinstance(result['delimiter'], str) and len(result['delimiter']) == 1, 'text_config')
    require(result['ocr_mode'] in ('auto', 'always') and type(result['ocr_dpi']) is int and result['ocr_dpi'] > 0
            and isinstance(result['ocr_language'], str), 'ocr_config')
    require(result['tessdata'] is None or isinstance(result['tessdata'], str), 'tessdata_config')
    require(isinstance(result['tables'], list), 'table_config')
    for table in result['tables']:
        require(isinstance(table, dict) and not set(table) - {'sheet', 'range', 'header_row'}, 'table_config_fields')
        require('header_row' not in table or type(table['header_row']) is int and table['header_row'] > 0, 'header_row_config')
        require('sheet' not in table or isinstance(table['sheet'], str), 'sheet_config')
        require('range' not in table or isinstance(table['range'], str), 'range_config')
    return result


def extract(path, settings):
    segments, warnings, metadata = [], [], {}

    def warn(code, location):
        warnings.append({'warning_id': f'w{len(warnings) + 1}', 'code': code, 'location': location})

    def add(raw, location, method='native', *, prose=False, extra=None):
        text = normalized(raw, prose)
        if text.strip():
            segments.append({'segment_id': f's{len(segments) + 1}', 'raw': raw, 'text': text,
                             'location': location, 'extraction_method': method, 'details': extra or {}})

    suffix = path.suffix.lower()
    if suffix == '.pdf':
        from app.ingestion.pdf import load_pdf
        from pathlib import Path
        manifest, raw_pages = {}, []
        load_pdf(path, ocr_mode=settings['ocr_mode'], ocr_language=settings['ocr_language'],
                 ocr_dpi=settings['ocr_dpi'], tessdata=Path(settings['tessdata']) if settings['tessdata'] else None,
                 manifest=manifest, raw_pages=raw_pages)
        for page in raw_pages:
            location = {'kind': 'pdf', 'page_start': page['page'], 'page_end': page['page']}
            add(page['raw'], location, page['extraction_method'], prose=True)
            if not page['raw'].strip():
                warn('empty_page', location)
        metadata = {'pymupdf_version': version('PyMuPDF'), 'ocr': manifest}
    elif suffix in ('.txt', '.md'):
        with path.open(encoding=settings['encoding'], newline='') as stream:
            raw = stream.read()
        lines = raw.splitlines(keepends=True)
        headings, pending, start, fence, code = [], [], 1, None, False

        def flush():
            if pending:
                add(''.join(pending), {'kind': 'text', 'line_start': start, 'line_end': start + len(pending) - 1,
                                      'headings': list(headings), 'code': code}, prose=not code)
                pending.clear()
        for number, line in enumerate(lines, 1):
            marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line) if suffix == '.md' else None
            heading = re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$', line) if suffix == '.md' and fence is None else None
            if marker and fence is None:
                flush()
                start, code, fence = number, True, marker[1]
                pending.append(line)
            elif fence is not None:
                pending.append(line)
                if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not line[marker.end():].strip():
                    flush()
                    fence, code = None, False
            elif heading:
                flush()
                level = len(heading[1])
                headings[:] = headings[:level - 1] + [normalized(heading[2])]
                start = number
                pending.append(line)
                flush()
            elif not line.strip():
                flush()
            else:
                if not pending:
                    start = number
                pending.append(line)
        flush()
        if fence:
            warn('unclosed_code_fence', {'line_start': start})
    elif suffix == '.csv':
        require(len(settings['tables']) <= 1 and all(not set(t) - {'header_row'} for t in settings['tables']), 'csv_table_config')
        rows = []
        with path.open(encoding=settings['encoding'], newline='') as stream:
            reader = csv.reader(stream, delimiter=settings['delimiter'], strict=True)
            previous = 0
            for row_number, values in enumerate(reader, 1):
                rows.append((row_number, values, {'line_start': previous + 1, 'line_end': reader.line_num}))
                previous = reader.line_num
        tables = settings['tables'] or [{}]
        header_row = tables[0].get('header_row', next((n for n, cells, _ in rows if any(c != '' for c in cells)), 0))
        require(header_row > 0 and header_row <= len(rows), 'table_header_missing')
        headers = rows[header_row - 1][1]
        for row_number, cells, location in rows:
            if not any(c != '' for c in cells):
                continue
            location |= {'kind': 'csv', 'row': row_number, 'header_row': header_row}
            text = '\n'.join(f'column_{i + 1} [{headers[i] if i < len(headers) else ""}]: {v}' for i, v in enumerate(cells))
            add(text, location, extra={'cells': cells, 'headers': headers, 'preamble': row_number < header_row})
            if len(cells) != len(headers):
                warn('unequal_column_count', location)
    elif suffix == '.xlsx':
        from openpyxl import load_workbook
        from openpyxl.utils.cell import range_boundaries, get_column_letter
        workbook = load_workbook(path, data_only=False, keep_links=False)
        values = None
        try:
            values = load_workbook(path, data_only=True, keep_links=False)
            metadata['openpyxl_version'] = version('openpyxl')
            tables = settings['tables'] or [{'sheet': sheet.title} for sheet in workbook]
            if settings['tables']:
                warn('explicit_table_selection', {'tables': tables})
            for table_index, table in enumerate(tables):
                require(table.get('sheet') in workbook.sheetnames, 'unknown_sheet')
                sheet = workbook[table['sheet']]
                limits = range_boundaries(table['range']) if 'range' in table else (1, 1, sheet.max_column, sheet.max_row)
                require(all(type(v) is int and v > 0 for v in limits), 'invalid_table_range')
                left, top, right, bottom = limits
                require(left <= right and top <= bottom and right <= 16384 and bottom <= 1048576, 'invalid_table_range')
                rows = list(sheet.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right))
                nonempty = [row for row in rows if any(c.value is not None for c in row)]
                if not nonempty:
                    warn('empty_sheet_or_range', {'sheet': sheet.title, 'range': table.get('range')})
                    continue
                header_row = table.get('header_row', nonempty[0][0].row)
                require(top <= header_row <= bottom, 'header_outside_range')
                headers = [stringify(sheet.cell(header_row, col).value) for col in range(left, right + 1)]
                if sheet.sheet_state != 'visible':
                    warn('hidden_sheet', {'sheet': sheet.title})
                merges = [str(r) for r in sheet.merged_cells.ranges]
                if merges:
                    warn('merged_cells', {'sheet': sheet.title, 'ranges': merges})
                for row in nonempty:
                    cells, rendered = [], []
                    for i, cell in enumerate(row):
                        cached = values[sheet.title][cell.coordinate].value
                        formula = cell.value if cell.data_type == 'f' else None
                        value = cached if formula else cell.value
                        missing = bool(formula) and cached is None
                        column = get_column_letter(left + i)
                        hidden = bool(sheet.row_dimensions[cell.row].hidden or any(
                            d.hidden and (d.min or 0) <= left + i <= (d.max or 0) for d in sheet.column_dimensions.values()))
                        cells.append({'coordinate': cell.coordinate, 'value': serial(value), 'type': cell.data_type,
                                      'formula': serial(formula), 'cached_value': serial(cached),
                                      'number_format': cell.number_format, 'hidden': hidden})
                        rendered.append(f'{column} [{headers[i]}]: ' + ('[formula result unavailable]' if missing else stringify(value)))
                        if missing:
                            warn('formula_without_cached_value', {'sheet': sheet.title, 'cell': cell.coordinate})
                        elif formula:
                            warn('formula_cached_value_requires_review', {'sheet': sheet.title, 'cell': cell.coordinate})
                        if hidden:
                            warn('hidden_cell', {'sheet': sheet.title, 'cell': cell.coordinate})
                    location = {'kind': 'xlsx', 'sheet': sheet.title, 'row': row[0].row, 'table_index': table_index,
                                'column_start': left, 'column_end': right, 'header_row': header_row}
                    add(f'Sheet: {sheet.title}\n' + '\n'.join(rendered), location,
                        extra={'cells': cells, 'headers': headers, 'merged_ranges': merges,
                               'sheet_state': sheet.sheet_state, 'preamble': row[0].row < header_row})
        finally:
            workbook.close()
            if values is not None:
                values.close()
    else:
        require(False, 'unsupported_format')
    require(bool(segments), 'no_content')
    return segments, warnings, metadata


def serial(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def stringify(value):
    return '' if value is None else str(serial(value))
