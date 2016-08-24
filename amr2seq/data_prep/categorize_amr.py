#!/usr/bin/python
import sys
import time
import pickle
import os
import cPickle
from amr_graph import *
from amr_utils import *
import logger
import argparse
from re_utils import *
from preprocess import *
from collections import defaultdict
def build_bimap(tok2frags):
    frag2map = defaultdict(set)
    index2frags = defaultdict(set)
    for index in tok2frags:
        for frag in tok2frags[index]:
            index2frags[index].add(frag)
            frag2map[frag].add(index)

    return (index2frags, frag2map)

#Here we try to make the tok to fragment mapping one to one
def rebuild_fragment_map(tok2frags):
    (index2frags, frag2map) = build_bimap(tok2frags)
    for index in tok2frags:
        if len(tok2frags[index]) > 1:
            new_frag_list = []
            min_frag = None
            min_length = 100
            for frag in tok2frags[index]:
                index_set = frag2map[frag]
                assert index in index_set
                if len(index_set) > 1:
                    if len(index_set) < min_length:
                        min_length = len(index_set)
                        min_frag = frag
                    index_set.remove(index)
                else:
                    new_frag_list.append(frag)
            if len(new_frag_list) == 0:
                assert min_frag is not None
                new_frag_list.append(min_frag)
            tok2frags[index] = new_frag_list
    return tok2frags

def mergeSpans(index_to_spans):
    new_index_to_spans = {}
    for index in index_to_spans:
        span_list = index_to_spans[index]
        span_list = sorted(span_list, key=lambda x:x[1])
        new_span_list = []
        curr_start = None
        curr_end = None

        for (idx, (start, end)) in enumerate(span_list):
            if curr_end is not None:
                #assert start >= curr_end, span_list
                if start < curr_end:
                    continue
                if start > curr_end: #There is a gap in between
                    new_span_list.append((curr_start, curr_end))
                    curr_start = start
                    curr_end = end
                else: #They equal, so update the end
                    curr_end = end

            else:
                curr_start = start
                curr_end = end

            if idx + 1 == len(span_list): #Have reached the last position
                new_span_list.append((curr_start, curr_end))
        new_index_to_spans[index] = new_span_list
    return new_index_to_spans

def extractNodeMapping(alignments, amr_graph):
    aligned_set = set()

    node_to_span = defaultdict(list)
    edge_to_span = defaultdict(list)

    num_nodes = len(amr_graph.nodes)
    num_edges = len(amr_graph.edges)

    op_toks = []
    role_toks = []
    for curr_align in reversed(alignments):
        curr_tok = curr_align.split('-')[0]
        curr_frag = curr_align.split('-')[1]

        span_start = int(curr_tok)
        span_end = span_start + 1

        aligned_set.add(span_start)

        (index_type, index) = amr_graph.get_concept_relation(curr_frag)
        if index_type == 'c':
            node_to_span[index].append((span_start, span_end))
            curr_node = amr_graph.nodes[index]

            #Extract ops for entities
            if len(curr_node.p_edges) == 1:
                par_edge = amr_graph.edges[curr_node.p_edges[0]]
                if 'op' == par_edge.label[:2]:
                    op_toks.append((span_start, curr_node.c_edge))

            if curr_node.is_named_entity():
                role_toks.append((span_start, curr_node.c_edge))

        else:
            edge_to_span[index].append((span_start, span_end))
    new_node_to_span = mergeSpans(node_to_span)
    new_edge_to_span = mergeSpans(edge_to_span)

    return (op_toks, role_toks, new_node_to_span, new_edge_to_span, aligned_set)

def extract_fragments(alignments, amr_graph):
    tok2frags = defaultdict(list)

    num_nodes = len(amr_graph.nodes)
    num_edges = len(amr_graph.edges)

    op_toks = []
    role_toks = []
    for curr_align in reversed(alignments):
        curr_tok = curr_align.split('-')[0]
        curr_frag = curr_align.split('-')[1]

        span_start = int(curr_tok)
        span_end = span_start + 1

        (index_type, index) = amr_graph.get_concept_relation(curr_frag)
        frag = AMRFragment(num_edges, num_nodes, amr_graph)
        if index_type == 'c':
            frag.set_root(index)
            curr_node = amr_graph.nodes[index]

            #Extract ops for entities
            if len(curr_node.p_edges) == 1:
                par_edge = amr_graph.edges[curr_node.p_edges[0]]
                if 'op' == par_edge.label[:2]:
                    op_toks.append((span_start, curr_node.c_edge))

            if curr_node.is_named_entity():
                role_toks.append((span_start, curr_node.c_edge))

            frag.set_edge(curr_node.c_edge)

        else:
            frag.set_edge(index)
            curr_edge = amr_graph.edges[index]
            frag.set_root(curr_edge.head)
            frag.set_node(curr_edge.tail)

        frag.build_ext_list()
        frag.build_ext_set()

        tok2frags[span_start].append(frag)

    for index in tok2frags:
        if len(tok2frags[index]) > 1:
            tok2frags[index] = connect_adjacent(tok2frags[index], logger)

    tok2frags = rebuild_fragment_map(tok2frags)
    for index in tok2frags:
        for frag in tok2frags[index]:
            frag.set_span(index, index+1)

    return (op_toks, role_toks, tok2frags)

#Verify this fragment contains only one edge and return it
def unique_edge(frag):
    #assert frag.edges.count() == 1, 'Not unify edge fragment found'
    amr_graph = frag.graph
    edge_list = []
    n_edges = len(frag.edges)
    for i in xrange(n_edges):
        if frag.edges[i] == 1:
            edge_list.append(i)
    assert len(edge_list) == frag.edges.count()
    return tuple(edge_list)

class AMR_stats(object):
    def __init__(self):
        self.num_reentrancy = 0
        self.num_predicates = defaultdict(int)
        self.num_nonpredicate_vals = defaultdict(int)
        self.num_consts = defaultdict(int)
        self.num_named_entities = defaultdict(int)
        self.num_entities = defaultdict(int)
        self.num_relations = defaultdict(int)

    def update(self, local_re, local_pre, local_non, local_con, local_ent, local_ne):
        self.num_reentrancy += local_re
        for s in local_pre:
            self.num_predicates[s] += local_pre[s]

        for s in local_non:
            self.num_nonpredicate_vals[s] += local_non[s]

        for s in local_con:
            self.num_consts[s] += local_con[s]

        for s in local_ent:
            self.num_entities[s] += local_ent[s]

        for s in local_ne:
            self.num_named_entities[s] += local_ne[s]
        #for s in local_rel:
        #    self.num_relations[s] += local_rel[s]

    def collect_stats(self, amr_graphs):
        for amr in amr_graphs:
            (named_entity_nums, entity_nums, predicate_nums, variable_nums, const_nums, reentrancy_nums) = amr.statistics()
            self.update(reentrancy_nums, predicate_nums, variable_nums, const_nums, entity_nums, named_entity_nums)

    def dump2dir(self, dir):
        def dump_file(f, dict):
            sorted_dict = sorted(dict.items(), key=lambda k:(-k[1], k[0]))
            for (item, count) in sorted_dict:
                print >>f, '%s %d' % (item, count)
            f.close()

        pred_f = open(os.path.join(dir, 'pred'), 'w')
        non_pred_f = open(os.path.join(dir, 'non_pred_val'), 'w')
        const_f = open(os.path.join(dir, 'const'), 'w')
        entity_f = open(os.path.join(dir, 'entities'), 'w')
        named_entity_f = open(os.path.join(dir, 'named_entities'), 'w')
        #relation_f = open(os.path.join(dir, 'relations'), 'w')

        dump_file(pred_f, self.num_predicates)
        dump_file(non_pred_f, self.num_nonpredicate_vals)
        dump_file(const_f, self.num_consts)
        dump_file(entity_f, self.num_entities)
        dump_file(named_entity_f, self.num_named_entities)
        #dump_file(relation_f, self.num_relations)

    def loadFromDir(self, dir):
        def load_file(f, dict):
            for line in f:
                item = line.strip().split(' ')[0]
                count = int(line.strip().split(' ')[1])
                dict[item] = count
            f.close()

        pred_f = open(os.path.join(dir, 'pred'), 'r')
        non_pred_f = open(os.path.join(dir, 'non_pred_val'), 'r')
        const_f = open(os.path.join(dir, 'const'), 'r')
        entity_f = open(os.path.join(dir, 'entities'), 'r')
        named_entity_f = open(os.path.join(dir, 'named_entities'), 'r')

        load_file(pred_f, self.num_predicates)
        load_file(non_pred_f, self.num_nonpredicate_vals)
        load_file(const_f, self.num_consts)
        load_file(entity_f, self.num_entities)
        load_file(named_entity_f, self.num_named_entities)

    def __str__(self):
        s = ''
        s += 'Total number of reentrancies: %d\n' % self.num_reentrancy
        s += 'Total number of predicates: %d\n' % len(self.num_predicates)
        s += 'Total number of non predicates variables: %d\n' % len(self.num_nonpredicate_vals)
        s += 'Total number of constants: %d\n' % len(self.num_consts)
        s += 'Total number of entities: %d\n' % len(self.num_entities)
        s += 'Total number of named entities: %d\n' % len(self.num_named_entities)

        return s

#Traverse AMR from top down, also categorize the sequence in case of alignment existed
def categorizeParallelSequences(amr, tok_seq, all_alignments, pred_freq_thre=50, var_freq_thre=50):

    old_depth = -1
    depth = -1
    stack = [(amr.root, TOP, None, 0)] #Start from the root of the AMR
    aux_stack = []
    seq = []

    cate_tok_seq = []
    ret_index = 0

    seq_map = {}   #Map each span to a category
    visited = set()

    covered = set()
    multiple_covered = set()

    node_to_label = {}
    cate_to_index = {}

    while stack:
        old_depth = depth
        curr_node_index, rel, parent, depth = stack.pop()
        curr_node = amr.nodes[curr_node_index]
        curr_var = curr_node.node_str()

        i = 0

        while old_depth - depth >= i:
            if aux_stack == []:
                import pdb
                pdb.set_trace()
            seq.append(aux_stack.pop())
            i+=1

        if curr_node_index in visited: #A reentrancy found
            seq.append((rel+LBR, None))
            seq.append((RET + ('-%d' % ret_index), None))
            ret_index += 1
            aux_stack.append((RBR+rel, None))
            continue

        visited.add(curr_node_index)
        seq.append((rel+LBR, None))

        exclude_rels, cur_symbol, categorized = amr.get_symbol(curr_node_index, pred_freq_thre, var_freq_thre)

        if categorized:
            node_to_label[curr_node_index] = cur_symbol

        seq.append((cur_symbol, curr_node_index))
        aux_stack.append((RBR+rel, None))

        for edge_index in reversed(curr_node.v_edges):
            curr_edge = amr.edges[edge_index]
            child_index = curr_edge.tail
            if curr_edge.label in exclude_rels:
                continue
            stack.append((child_index, curr_edge.label, curr_var, depth+1))

    seq.extend(aux_stack[::-1])
    cate_span_map, end_index_map, covered_toks = categorizedSpans(all_alignments, node_to_label)

    map_seq = []
    nodeindex_to_tokindex = {}  #The mapping
    label_to_index = defaultdict(int)

    for tok_index, tok in enumerate(tok_seq):
        if tok_index not in covered_toks:
            cate_tok_seq.append(tok)
            continue

        if tok_index in end_index_map: #This span can be mapped to category
            end_index = end_index_map[tok_index]
            assert (tok_index, end_index) in cate_span_map

            node_index, aligned_label = cate_span_map[(tok_index, end_index)]

            if node_index not in nodeindex_to_tokindex:
                nodeindex_to_tokindex[node_index] = defaultdict(int)

            indexed_aligned_label = '%s-%d' % (aligned_label, label_to_index[aligned_label])
            nodeindex_to_tokindex[node_index] = indexed_aligned_label
            label_to_index[aligned_label] += 1


            cate_tok_seq.append(indexed_aligned_label)

            align_str = '%d-%d:%s:%d:%s:%s' % (tok_index, end_index, ' '.join(tok_seq[tok_index:end_index]), node_index, amr.nodes[node_index].node_str(), indexed_aligned_label)
            map_seq.append(align_str)

    #print 'original:'
    #print seq
    seq = [nodeindex_to_tokindex[node_index] if node_index in nodeindex_to_tokindex else label for (label, node_index) in seq]
    #print seq
    #print cate_tok_seq, map_seq
    #print nodeindex_to_tokindex

    return seq, cate_tok_seq, map_seq

def categorizedSpans(all_alignments, node_to_label):
    visited = set()

    all_alignments = sorted(all_alignments.items(), key=lambda x:len(x[1]))
    span_map = {}
    end_index_map = {}

    for (node_index, aligned_spans) in all_alignments:
        if node_index in node_to_label:
            aligned_label = node_to_label[node_index]
            for (span_start, span_end) in aligned_spans:
                span_set = set(xrange(span_start, span_end))
                if len(span_set & visited) != 0:
                    continue

                visited |= span_set

                span_map[(span_start, span_end)] = (node_index, aligned_label)
                end_index_map[span_start] = span_end

    return span_map, end_index_map, visited

def linearize_amr(args):
    logger.file = open(os.path.join(args.run_dir, 'logger'), 'w')

    amr_file = os.path.join(args.data_dir, 'amr')
    alignment_file = os.path.join(args.data_dir, 'alignment')
    if args.use_lemma:
        tok_file = os.path.join(args.data_dir, 'lemmatized_token')
    else:
        tok_file = os.path.join(args.data_dir, 'token')
    pos_file = os.path.join(args.data_dir, 'pos')

    collapsed_tok_file = os.path.join(args.data_dir, 'collapsed_token')
    collapsed_graph_file = os.path.join(args.data_dir, 'collased_amr')
    map_file = os.path.join(args.data_dir, 'map')

    amr_graphs = load_amr_graphs(amr_file)
    alignments = [line.strip().split() for line in open(alignment_file, 'r')]
    toks = [line.strip().split() for line in open(tok_file, 'r')]
    poss = [line.strip().split() for line in open(pos_file, 'r')]

    assert len(amr_graphs) == len(alignments) and len(amr_graphs) == len(toks) and len(amr_graphs) == len(poss), '%d %d %d %d %d' % (len(amr_graphs), len(alignments), len(toks), len(poss))

    num_self_cycle = 0
    used_sents = 0

    amr_statistics = AMR_stats()

    if args.use_stats:
        amr_statistics.loadFromDir(args.stats_dir)
        print amr_statistics
    else:
        os.system('mkdir -p %s' % args.stats_dir)
        amr_statistics.collect_stats(amr_graphs)
        amr_statistics.dump2dir(args.stats_dir)

    singleton_num = 0.0
    multiple_num = 0.0
    total_num = 0.0
    empty_num = 0.0

    amr_seq_file = os.path.join(args.run_dir, 'amrseq')
    tok_seq_file = os.path.join(args.run_dir, 'tokseq')
    map_seq_file = os.path.join(args.run_dir, 'mapseq')

    amrseq_wf = open(amr_seq_file, 'w')
    tokseq_wf = open(tok_seq_file, 'w')
    mapseq_wf = open(map_seq_file, 'w')

    for (sent_index, (tok_seq, pos_seq, alignment_seq, amr)) in enumerate(zip(toks, poss, alignments, amr_graphs)):

        logger.writeln('Sentence #%d' % (sent_index+1))
        logger.writeln(' '.join(tok_seq))

        amr.setStats(amr_statistics)

        edge_alignment = bitarray(len(amr.edges))
        if edge_alignment.count() != 0:
            edge_alignment ^= edge_alignment
        assert edge_alignment.count() == 0

        has_cycle = False
        if amr.check_self_cycle():
            num_self_cycle += 1
            has_cycle = True

        amr.set_sentence(tok_seq)
        amr.set_poss(pos_seq)

        aligned_fragments = []
        reentrancies = {}  #Map multiple spans as reentrancies, keeping only one as original, others as connections

        has_multiple = False
        no_alignment = False

        aligned_set = set()

        (opt_toks, role_toks, node_to_span, edge_to_span, temp_aligned) = extractNodeMapping(alignment_seq, amr)

        temp_unaligned = set(xrange(len(pos_seq))) - temp_aligned

        all_frags = []
        all_alignments = defaultdict(list)

        ####Extract entities mapping#####
        for (frag, frag_label) in amr.extract_entities():
            if len(opt_toks) == 0:
                logger.writeln("No alignment for the entity found")

            (aligned_indexes, entity_spans) = all_aligned_spans(frag, opt_toks, role_toks, temp_unaligned)
            root_node = amr.nodes[frag.root]

            entity_mention_toks = root_node.namedEntityMention()
            print 'fragment entity mention: %s' % ' '.join(entity_mention_toks)

            total_num += 1.0
            if entity_spans:
                entity_spans = removeRedundant(tok_seq, entity_spans, entity_mention_toks)
                if len(entity_spans) == 1:
                    singleton_num += 1.0
                    logger.writeln('Single fragment')
                    for (frag_start, frag_end) in entity_spans:
                        logger.writeln(' '.join(tok_seq[frag_start:frag_end]))
                        all_alignments[frag.root].append((frag_start, frag_end))
                else:
                    multiple_num += 1.0
                    logger.writeln('Multiple fragment')
                    logger.writeln(aligned_indexes)
                    logger.writeln(' '.join([tok_seq[index] for index in aligned_indexes]))

                    for (frag_start, frag_end) in entity_spans:
                        logger.writeln(' '.join(tok_seq[frag_start:frag_end]))
                        all_alignments[frag.root].append((frag_start, frag_end))
            else:
                empty_num += 1.0
                _ = all_alignments[frag.root]

        for node_index in node_to_span:
            if node_index in all_alignments:
                continue

            all_alignments[node_index] = node_to_span[node_index]
            if len(node_to_span[node_index]) > 1:
                print 'Multiple found:'
                print amr.nodes[node_index].node_str()
                for (span_start, span_end) in node_to_span[node_index]:
                    print ' '.join(tok_seq[span_start:span_end])

        ##Based on the alignment from node index to spans in the string

        assert len(tok_seq) == len(pos_seq)

        amr_seq, cate_tok_seq, map_seq = categorizeParallelSequences(amr, tok_seq, all_alignments, args.min_prd_freq, args.min_var_freq)
        print >> amrseq_wf, ' '.join(amr_seq)
        print >> tokseq_wf, ' '.join(cate_tok_seq)
        print >> mapseq_wf, ' '.join(map_seq)

    amrseq_wf.close()
    tokseq_wf.close()
    mapseq_wf.close()

    print "one to one alignment: %lf" % (singleton_num/total_num)
    print "one to multiple alignment: %lf" % (multiple_num/total_num)
    print "one to empty alignment: %lf" % (empty_num/total_num)

if __name__ == '__main__':
    argparser = argparse.ArgumentParser()

    argparser.add_argument("--amr_file", type=str, help="the original AMR graph files", required=False)
    argparser.add_argument("--stop", type=str, help="stop words file", required=False)
    argparser.add_argument("--lemma", type=str, help="lemma file", required=False)
    argparser.add_argument("--data_dir", type=str, help="the data directory for dumped AMR graph objects, alignment and tokenized sentences")
    argparser.add_argument("--run_dir", type=str, help="the output directory for saving the constructed forest")
    argparser.add_argument("--use_lemma", action="store_true", help="if use lemmatized tokens")
    argparser.add_argument("--use_stats", action="store_true", help="if use a built-up statistics")
    argparser.add_argument("--stats_dir", type=str, help="the statistics directory")
    argparser.add_argument("--min_prd_freq", type=int, default=50, help="threshold for filtering predicates")
    argparser.add_argument("--min_var_freq", type=int, default=50, help="threshold for filtering non predicate variables")
    argparser.add_argument("--index_unknown", action="store_true", help="if to index the unknown predicates or non predicate variables")

    args = argparser.parse_args()
    linearize_amr(args)