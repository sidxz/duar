import { describe, it, expect } from 'vitest'
import { encodeHeaderValue, decodeHeaderValue } from '../header-codec'

describe('header codec', () => {
  it('roundtrips display names, including non-Latin-1', () => {
    for (const name of ['Test User', 'Zoë', '中文名', 'Иван Петров', '100% Bob', '']) {
      expect(decodeHeaderValue(encodeHeaderValue(name))).toBe(name)
    }
  })

  it('encoded values are Headers-safe (ByteString limit)', () => {
    // Premise: raw non-Latin-1 values throw in WHATWG Headers…
    expect(() => new Headers().set('x-duar-name', '中文名')).toThrow()
    // …encoded values do not.
    expect(() =>
      new Headers().set('x-duar-name', encodeHeaderValue('中文名')),
    ).not.toThrow()
  })

  it('decode passes through values it did not encode', () => {
    expect(decodeHeaderValue('not%encoded')).toBe('not%encoded')
  })
})
