#!/usr/bin/python
"""
This is a pcfg of almost the following grammar:
W -> <english-word>L | <name>L
D -> <date> | <phone-no> | [0-9]+
Y -> [^\W]+  # symbol
K -> <keyboard-sequence>
R -> repeat
S -> sequence # 123456, abcdef, ABCDEFG
L -> Capitalize | ALL-UPPER | all-lower | l33t
G -> <some-combination-of-those-NonTs>
"""

import os, sys, string
BASE_DIR = os.getcwd()
sys.path.append(BASE_DIR)
from dawg import IntDAWG, DAWG
import marisa_trie
import struct, json, bz2, re
from lexer_helper import Date, KeyBoard, RuleSet, ParseTree
from helper.helper import open_, getIndex, convert2group
from Crypto.Random import random
import honeyvault_config as hny_config
from honeyvault_config import NONTERMINAL, TERMINAL, MIN_COUNT
from honeyvault_config import MEMLIMMIT, GRAMMAR_DIR
from collections import OrderedDict, defaultdict
from pprint import pprint
import resource  # For checking memory usage
from lexer import NonT_L

grammar_file = GRAMMAR_DIR + '/grammar.cfg.bz2'


class TrainedGrammar(object):

    l33t_replaces = DAWG.compile_replaces({
            '3':'e', '4':'a', '@':'a',
            '$':'s', '0':'o', '1':'i',
            'z':'s'
            })

    def __init__(self, g_file=grammar_file):
        self.load(g_file)
        self.NonT_set = filter(lambda x: x.find('_') < 0,  
                               self.G.keys())

    def load(self, filename):
        self.G = json.load(open_(filename),
                           object_pairs_hook=OrderedDict)
        for k,v in self.G.items():
            v['__total__'] = sum(v.values())
        self.Wdawg = IntDAWG(self.G['W'].items())

    def get_prob(self, l, r):
        f = self.G.get(l, {}).get(r, 0)
        if f>0:
            return float(f)/self.G[l]['__total__']

    def isNonTerm(self, lhs): # this means given lhs, rhs will be in NonT 
        return lhs in self.NonT_set

    def get_actual_NonTlist(self, lhs, rhs):
        if lhs == 'G':
            return list(rhs)
        elif lhs in ['L', 'T']:
            return ['%s_%s' % (lhs,c)
                    for c in rhs]
        else:
            return []

    def get_freq(self, l, r):
        return self.G.get(l, {}).get(r, 0)

    def get_W_rule(self, word):
        w = unicode(word.lower())
        k = self.Wdawg.similar_keys(w, self.l33t_replaces)
        if k:
            k = k[0]
            L = NonT_L(k, word)
            return ('W', [(k, L)], self.get_prob('W', k))

    def get_T_rule(self, word):
        T = Date(word)
        if T:
            p = 10**(len(word)-8)
            # for r in T.tree:
            #     p *= self.get_prob(*r)
            # p *= self.get_prob(*(T.get_rule()))
            return ('T', [(word, T)], p)

    def get_all_matches(self, word):
        rules = []
        for nt in self.NonT_set:
            if nt == 'W':
                l = self.get_W_rule(word)
                if l: rules.append(l)
            elif nt == 'T':
                l = self.get_T_rule(word)
                if l: rules.append(l)
            else:
                f = self.G[nt].get(word, 0)
                if f>0:
                    rules.append((nt, [(word)], float(f)/self.G[nt]['__total__']))
        rules = filter(lambda x: x and x[-1], rules)
        if rules:
            return max(rules, key=lambda x: x[-1])

    def join(self, r, s):
        if (r and s and
            not r[0].startswith('L_') and
            not s[0].startswith('L_') and
            not r[0].startswith('T_') and
            not s[0].startswith('T_') ):
            k = r[0] + s[0]
            p = r[-1] * s[-1]
            a = r[1] + s[1]
            return (k, a, p)

    def parse(self, word):
        A = {}
        for j in range(len(word)):
            for i in range(len(word)-j):
                A[(i, i+j)] = self.get_all_matches(word[i:j+i+1])
                t = [A[(i, i+j)]]
                t.extend([self.join(A[(i,k)], A[(k+1, i+j)])
                          for k in range(i, i+j)])
                if t:
                    A[(i, i+j)] = \
                        max(t, key = lambda x: x[-1] if x else 0)
                else:
                    A[(i, i+j)] = ()
                    # print "Not sure why it reached here. But it did!"
                    # print i, j, word[i: i+j+1]
        return A[(0, len(word)-1)]
    
    def l_parse_tree(self, word): # leftmost parse-tree
        pt = ParseTree()
        p = self.parse(word)
        if not p:
            print "Failing at ", word.encode('utf-8')
            return pt
        pt.add_rule(('G', p[0]))
        for l, each_r in zip(p[0], p[1]):
            if isinstance(each_r, basestring):
                pt.add_rule((l, each_r))
            elif l == 'W':
                pt.add_rule((l, each_r[0]))
                L_parse_tree = each_r[1].parse_tree()
                pt.add_rule(L_parse_tree[0])
                if len(L_parse_tree.tree)>1:
                    pt.tree.extend(L_parse_tree[1][1])
            elif l == 'T':
                p = each_r[1].parse_tree()
                rule_name = ''.join([r[0].replace('T_','')
                                     for r in p])
                pt.add_rule((l, rule_name))
                pt.extend_rule(p)
            else:
                print "Something is severly wrong"
        return pt

    def rule_set(self, word):
        rs = RuleSet()
        pt = self.l_parse_tree(word)
        for p in pt.tree:
            rs.add_rule(*p)
        return rs

    def encode_rule(self, l, r):
        rhs_dict = self.G[l]
        i = rhs_dict.keys().index(r)
        assert i >= 0
        l = sum(rhs_dict.values()[:i])
        r = l + rhs_dict[r]
        return convert2group(random.randint(l,r),
                             rhs_dict['__total__'])

    def encode_pw(self, pw):
        pt = self.l_parse_tree(pw)
        code_g = [self.encode_rule(*p)
                  for p in pt]
        extra = hny_config.PASSWORD_LENGTH - len(code_g);
        code_g.extend([convert2group(0,1) for x in range(extra)])
        return code_g

    def decode_rule(self, l, p):
        rhs_dict = self.G[l]
        p %= rhs_dict['__total__']
        for k,v in rhs_dict.items():
            if p<v:
                return k
            else:
                p -= v
        print "Allas could not find.", l, p

    def decode_l33t(self, w, iterp):
        l = self.decode_rule('L', iterp.next())
        if l == 'Caps': return w.capitalize()
        elif l == 'lower': return w.lower()
        elif l == 'UPPER': return w.upper()
        else: 
            nw = ''.join([self.decode_rule('L_%s'%c, iterp.next())
                   for c in w])
            return nw
                
    def decode_pw(self, P):
        assert len(P) == hny_config.PASSWORD_LENGTH
        iterp = iter(P)
        plaintext = '';
        stack = ['G']
        while stack:
            lhs = stack.pop()
            rhs = self.decode_rule(lhs, iterp.next())
            if lhs in ['G', 'T']:
                arr = list(rhs) if lhs == 'G' \
                    else ['T_%s'% c for c in rhs]
                arr.reverse()
                stack.extend(arr)
            elif lhs == 'W':
                rhs = self.decode_l33t(rhs, iterp)
                plaintext += rhs
            else:
                plaintext += rhs
        return plaintext

    def __getitem__(self, l):
        return self.G[l]

    def __contains__(self, k):
        return k in self.G


class SubGrammar(TrainedGrammar):
    def __init__(self, base_pcfg):
        R = RuleSet()
        self.base_pcfg = base_pcfg
        R.update_set(RuleSet(d={'L': self.base_pcfg['L']}))
        for c in string.ascii_lowercase:
            x = 'L_%s' % c
            R.update_set(RuleSet(d={x: self.base_pcfg[x]}))
        self.R = R
        self.G = R.G
        self.freeze = False

    def add_rule(self, l, r):
        if self.freeze:
            print "Warning! Please defreeze the grammar before adding"
        self.R.add_rule(l,r)

    def finalize(self):
        self.fix_freq()
        self.NonT_set = filter(lambda x: x.find('_') < 0,  
                               self.G.keys()) + 'Yymd'.split()
        self.G = self.R.G
        self.Wdawg = IntDAWG(self.G['W'].items())
        self.freeze = True

    def reset(self):
        for k,v in self.G.items():
            if '__total__' in v:
                del v['__total__']
        self.freeze = False
        
    def add_some_extra_rules(self):
        for k,v in self.R.items():
            pass
            
    def update_grammar(self, *args):
        self.reset()
        for pw in args:
            pw = pw.replace('\\', '')
            self.R.update_set(self.base_pcfg.rule_set(pw))
        self.finalize()

    def fix_freq(self):
        for l,v in self.R.items():
            s = 0
            for r in v:
                if r != '__total__':
                    v[r] = self.base_pcfg.get_freq(l,r)
                    s += v[r]
            v['__total__'] = s
    
    def __str__(self):
        return str(self.R)
    
    def __eq__(self, newG):
        return self.G == newG.G

if __name__=='__main__':
    tg = TrainedGrammar()
    if sys.argv[1] == '-pw':
        code_g =  tg.encode_pw(sys.argv[2])
        print code_g
        print tg.decode_pw(code_g)
    elif sys.argv[1] == '-vault':
        g = SubGrammar(tg, sys.argv[2:])
        print g
    elif sys.argv[1] == '-parse':
        print 'Parse',  tg.parse(sys.argv[2])
