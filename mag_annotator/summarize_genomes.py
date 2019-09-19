import pandas as pd
from collections import Counter
from os import path, mkdir
import re
import altair as alt

from mag_annotator.utils import get_database_locs

# TODO: add RBH information to output
# TODO: add measure of redendancy of genes
# TODO: add tqdm progress bar

FRAME_COLUMNS = ['gene_id', 'gene_description', 'module', 'sheet', 'header', 'subheader']
RRNA_TYPES = ['5S rRNA', '16S rRNA', '23S rRNA']


def get_ids_from_annotation(frame):
    id_list = list()
    # get kegg ids
    id_list += [j for i in frame.kegg_id.dropna() for j in i.split(',')]
    # get ec numbers
    for kegg_hit in frame.kegg_hit.dropna():
        id_list += [i[1:-1] for i in re.findall(r'\[EC:\d*.\d*.\d*.\d*\]', kegg_hit)]
    # get merops ids
    id_list += [j for i in frame.peptidase_family.dropna() for j in i.split(';')]
    # get cazy ids
    id_list += [j.split(' ')[0] for i in frame.cazy_hits.dropna() for j in i.split(';')]
    return Counter(id_list)


def fill_genome_summary_frame(annotations, genome_summary_frame, groupby_column):
    for genome, frame in annotations.groupby(groupby_column):
        id_dict = get_ids_from_annotation(frame)
        genome_summary_frame[genome] = [id_dict[i] if i in id_dict else 0 for i in genome_summary_frame.gene_id]
    return genome_summary_frame


def summarize_rrnas(rrnas_df, groupby_column='fasta'):
    genome_rrna_dict = dict()
    for genome, frame in rrnas_df.groupby(groupby_column):
        genome_rrna_dict[genome] = Counter(frame['type'])
    row_list = list()
    for rna_type in RRNA_TYPES:
        row = [rna_type, '%s ribosomal RNA gene' % rna_type.split()[0], 'rRNA', 'rRNA', '', '']
        for genome, rrna_dict in genome_rrna_dict.items():
            row.append(genome_rrna_dict[genome].get(type, 0))
        row_list.append(row)
    rrna_frame = pd.DataFrame(row_list, columns=FRAME_COLUMNS+list(genome_rrna_dict.keys()))
    return rrna_frame


def summarize_trnas(trnas_df, groupby_column='fasta'):
    # first build the frame
    combos = set()
    for index, line in trnas_df.iterrows():
        combos.add((line.Type, line.Codon, line.Note))
    frame_rows = list()
    for combo in combos:
        if combo[2] == 'pseudo':
            gene_id = '%s, pseudo (%s)'
            gene_description = '%s pseudo tRNA with %s Codon'
        else:
            gene_id = '%s (%s)'
            gene_description = '%s pseudo tRNA with %s Codon'
        gene_id = gene_id % (combo[0], combo[1])
        gene_description = gene_description % (combo[0], combo[1])
        module_description = '%s tRNA' % combo[0]
        frame_rows.append([gene_id, gene_description, module_description, 'tRNA', 'tRNA', ''])
    trna_frame = pd.DataFrame(frame_rows, columns=FRAME_COLUMNS)
    trna_frame = trna_frame.sort_values('gene_id')
    # then fill it in
    trna_frame = trna_frame.set_index('gene_id')
    for group, frame in trnas_df.groupby(groupby_column):
        gene_ids = list()
        for index, line in frame.iterrows():
            if line.Note == 'pseudo':
                gene_id = '%s, pseudo (%s)'
            else:
                gene_id = '%s (%s)'
            gene_ids.append(gene_id % (line.Type, line.Codon))
        trna_frame[group] = pd.Series(Counter(gene_ids))
    trna_frame = trna_frame.reset_index()
    trna_frame = trna_frame.fillna(0)
    return trna_frame


def make_genome_summary(annotations, genome_summary_frame, output_file, trna_frame=None, rrna_frame=None,
                        groupby_column='fasta', remove_empty_rows=False, remove_empty_cols=False):
    summary_frames = list()
    # get ko summaries
    summary_frames.append(fill_genome_summary_frame(annotations, genome_summary_frame.copy(), groupby_column))

    # add rRNAs
    if rrna_frame is not None:
        summary_frames.append(summarize_rrnas(rrna_frame, groupby_column))

    # add tRNAs
    if trna_frame is not None:
        summary_frames.append(summarize_trnas(trna_frame, groupby_column))

    # merge summary frames
    summarized_genomes = pd.concat(summary_frames, sort=False)

    # post processing
    summarized_genomes_numbers_only = summarized_genomes[summarized_genomes.columns[7:]]
    if remove_empty_rows:  # filter out empty rows and columns if viral
        # remove all zero rows for viral
        summarized_genomes = summarized_genomes.loc[summarized_genomes_numbers_only.sum(axis=1) > 0]
    if remove_empty_cols:
        # remove all zero columns so viruses with no AMGs
        good_columns = summarized_genomes_numbers_only.columns[summarized_genomes_numbers_only.sum(axis=0) > 0]
        summarized_genomes = summarized_genomes[list(summarized_genomes.columns[:7]) + list(good_columns)]

    # turn all this into an xlsx
    with pd.ExcelWriter(output_file) as writer:
        for sheet, frame in summarized_genomes.groupby('sheet', sort=False):
            frame = frame.sort_values(['header', 'subheader', 'module', 'gene_id'])
            frame = frame.drop(['sheet'], axis=1)
            frame = frame.dropna(axis=1, how='all')
            frame.to_excel(writer, sheet_name=sheet, index=False)


def make_genome_stats(annotations, rrna_frame=None, trna_frame=None, group_column='fasta'):
    rows = list()
    columns = ['genome', 'number of scaffolds', 'taxonomy', 'completeness', 'contamination']
    if rrna_frame is not None:
        columns += RRNA_TYPES
    if trna_frame is not None:
        columns.append('tRNA count')
    for genome, frame in annotations.groupby(group_column):
        row = [genome, len(set(frame['scaffold']))]
        if 'bin_taxonomy' in frame:
            row.append(frame['bin_taxonomy'][0])
        if 'bin_completeness' in frame:
            row.append(frame['bin_completeness'][0])
        if 'bin_contamination' in frame:
            row.append(frame['bin_contamination'][0])
        if rrna_frame is not None:
            genome_rrnas = rrna_frame.loc[rrna_frame.fasta == genome]
            for rrna in RRNA_TYPES:
                sixteens = genome_rrnas.loc[genome_rrnas.type == rrna]
                if sixteens.shape[0] == 0:
                    row.append('')
                elif sixteens.shape[0] == 1:
                    row.append('%s, (%s, %s)' % (sixteens.index[0], sixteens.begin[0], sixteens.end[0]))
                else:
                    row.append('%s present' % sixteens.shape[0])
        if trna_frame is not None:
            row.append(trna_frame.loc[trna_frame[group_column] == genome].shape[0])
        rows.append(row)
    genome_stats = pd.DataFrame(rows, columns=columns)
    return genome_stats


def make_functional_heatmap(annotations, function_heatmap_form, groupby_column='fasta'):
    # build dict of ids per genome
    genome_to_id_dict = dict()
    for genome, frame in annotations.groupby(groupby_column):
        id_list = get_ids_from_annotation(frame).keys()
        genome_to_id_dict[genome] = set(id_list)
    # build long from data frame
    rows = list()
    for _, row in function_heatmap_form.iterrows():
        function_id_set = set([i.strip() for i in row.function_ids.split(', ')])
        for bin_name, id_set in genome_to_id_dict.items():
            present_in_bin = len(set.intersection(id_set, function_id_set)) > 0
            rows.append(list(row) + [bin_name, present_in_bin])
    long_frame = pd.DataFrame(rows, columns=list(function_heatmap_form.columns) + ['bin', 'present'])
    # build heatmap
    row_height = 10
    column_width = 10

    charts = list()
    grouped_function_names = long_frame.groupby('category', sort=False)
    for i, (group, frame) in enumerate(grouped_function_names):
        c = alt.Chart().encode(
            y=alt.Y('function_name', title=group, axis=alt.Axis(titleAngle=270, titleAlign='center'),
                    sort=list(function_heatmap_form.loc[function_heatmap_form['category'] == group]['category'])),
            tooltip=[alt.Tooltip('bin', title='MAG'),
                     alt.Tooltip('category', title='Category'),
                     alt.Tooltip('subcategory', title='Subcategory'),
                     alt.Tooltip('function_name', title='Function'),
                     alt.Tooltip('long_function_name', title='Description'),
                     alt.Tooltip('gene_symbol', title='Gene Symbol')]
        )
        num_function_names_in_category = len(set(frame.function_name))
        num_mags_in_frame = len(set(frame.bin))
        a = c.mark_rect().encode(x='bin',
                                 color=alt.Color('present', legend=alt.Legend(title="Function is Present",
                                                                              symbolType='square',
                                                                              values=[True, False])),
                                 ).properties(
            width=column_width * num_mags_in_frame,
            height=row_height * num_function_names_in_category)
        if i + 1 == len(grouped_function_names):
            b = c.mark_text().encode(x=alt.X('bin', title='MAG'))
        else:
            b = c.mark_text().encode(x=alt.X('bin', axis=alt.Axis(title=None, labels=False, ticks=False)))
        mini_function_name_heatmap = alt.layer(a, b, data=frame)
        charts.append(mini_function_name_heatmap)

    function_heatmap = alt.vconcat(*charts)
    return function_heatmap


def summarize_genomes(input_file, trna_path, rrna_path, output_dir, groupby_column, viral=False):
    # read in data
    annotations = pd.read_csv(input_file, sep='\t', index_col=0)
    if trna_path is None:
        trna_frame = None
    else:
        trna_frame = pd.read_csv(trna_path, sep='\t', index_col=0)
    if rrna_path is None:
        rrna_frame = None
    else:
        rrna_frame = pd.read_csv(rrna_path, sep='\t', index_col=0)

    # get db_locs and read in dbs
    db_locs = get_database_locs()
    if 'genome_summary_form' not in db_locs:
        raise ValueError('Genome summary form location must be set in order to summarize genomes')
    if 'function_heatmap_form' not in db_locs:
        raise ValueError('Functional heat map location must be set in order to summarize genomes')

    # read in dbs
    genome_summary_form = pd.read_csv(db_locs['genome_summary_form'], sep='\t')
    function_heatmap_form = pd.read_csv(db_locs['function_heatmap_form'], sep='\t')

    # make output folder
    mkdir(output_dir)

    # make genome metabolism summary
    make_genome_summary(annotations, genome_summary_form, trna_frame, rrna_frame, groupby_column)

    # make genome stats
    if not viral:
        genome_stats = make_genome_stats(annotations, rrna_frame, trna_frame, groupby_column)
        genome_stats.to_csv(path.join(output_dir, 'genome_stats.tsv'), sep='\t', index=False)

    # make functional heatmap
    function_heatmap = make_functional_heatmap(annotations, function_heatmap_form, groupby_column)
    function_heatmap.save(path.join(output_dir, 'function_heatmap.html'))
